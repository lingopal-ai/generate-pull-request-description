import argparse
import logging
import re
import subprocess

import requests

logger = logging.getLogger(__name__)


CONVENTIONAL_COMMIT_BREAKING_CHANGE_INDICATORS = {"BREAKING-CHANGE", "BREAKING CHANGE"}

LAST_RELEASE = "LAST_RELEASE"
PULL_REQUEST_START = "PULL_REQUEST_START"
STOP_POINTS = (LAST_RELEASE, PULL_REQUEST_START)

BREAKING_CHANGE_INDICATOR = "💥 **BREAKING CHANGE:** "
UPGRADE_INSTRUCTIONS_HEADER = "# 🔄 Upgrade instructions"

COMMIT_REF_MERGE_PATTERN = re.compile(r"Merge [0-9a-f]+ into [0-9a-f]+")
SEMANTIC_VERSION_PATTERN = re.compile(r"tag: (\d+\.\d+\.\d+)")
CONVENTIONAL_COMMIT_PATTERN = re.compile(
    r"^(?P<type>[a-zA-Z]+)(?:\((?P<scope>[^)]+)\))?:"
)

OTHER_SECTION_HEADING = "### 🔀 Other"
UNCATEGORISED_SECTION_HEADING = "### ❓ Uncategorised!"

COMMIT_CODES_TO_HEADINGS_MAPPING = {
    "feat": "## ✨ New Features",
    "fix": "## 🐛 Bug Fixes",
    "docs": "## 📚 Documentation",
    "style": "## 💅 Style",
    "refactor": "## ♻️ Refactoring",
    "perf": "## ⚡️ Performance Improvements",
    "test": "## 🧪 Tests",
    "build": "## 🏗️ Build System",
    "ci": "## 🤖 CI",
    "chore": "## 🧹 Chores",
    # Legacy mappings for backward compatibility
    "FEA": "## ✨ New features",
    "ENH": "## 🚀 Enhancements",
    "FIX": "## 🐛 Fixes",
    "OPS": "## 🔧 Operations",
    "DEP": "## 📦 Dependencies",
    "REF": "## ♻️ Refactoring",
    "TST": "## 🧪 Testing",
    "MRG": "## 🔀 Other",
    "REV": "## ⏮️ Reversions",
    "CHO": "## 🧹 Chores",
    "STY": "## 💅 Style",
    "WIP": "## 🚧 Other",
    "DOC": "## 📚 Other",
}

BREAKING_CHANGE_COUNT_KEY = "BREAKING CHANGE COUNT"

AUTO_GENERATION_START_INDICATOR = "<!--- START AUTOGENERATED NOTES --->"
AUTO_GENERATION_END_INDICATOR = "<!--- END AUTOGENERATED NOTES --->"
SKIP_INDICATOR = "<!--- SKIP AUTOGENERATED NOTES --->"


class PullRequestDescriptionGenerator:
    """A pull request description generator that can be used to generate release notes. The notes are pulled together
    from Conventional Commit messages, stopping at the specified stop point. The stop point can either be the last
    merged pull request in the branch or the last semantically-versioned release tagged in the branch. If previous
    notes are provided, only the text between the comment lines `<!--- START AUTOGENERATED NOTES --->` and
    `<!--- END AUTOGENERATED NOTES --->` will be replaced - anything outside of this will appear in the new release
    notes.

    :param str stop_point: the point in the git history up to which commit messages should be used - should be either "LAST_RELEASE" or "PULL_REQUEST_START"
    :param str|None pull_request_url: GitHub API URL for the pull request - this can be accessed in a GitHub workflow as ${{ github.event.pull_request.url }}
    :param str|None api_token: GitHub API token - this can be accessed in a GitHub workflow as ${{ secrets.GITHUB_TOKEN }}
    :param str header: the header to put above the autogenerated release notes, including any markdown styling (defaults to "# Contents")
    :param str list_item_symbol: the markdown symbol to use for listing the commit messages in the release notes (defaults to a ticked checkbox but could be a bullet point or number)
    :param dict|None commit_codes_to_headings_mapping: mapping of commit codes to the header they should be put under, including markdown styling (e.g. "### Fixes")
    :param bool include_link_to_pull_request: if `True`, link to the given pull request in the release notes; ignore if no pull request URL is given
    :return None:
    """

    def __init__(
        self,
        stop_point,
        pull_request_url=None,
        api_token=None,
        header="# Changelog",
        list_item_symbol="-",
        commit_codes_to_headings_mapping=None,
        include_link_to_pull_request=True,
    ):
        if stop_point.upper() not in STOP_POINTS:
            raise ValueError(
                f"`stop_point` must be one of {STOP_POINTS!r}; received {stop_point!r}."
            )

        self.stop_point = stop_point.upper()

        self.current_pull_request = None
        self.previous_notes = None

        if pull_request_url:
            self.current_pull_request = self._get_current_pull_request(
                pull_request_url, api_token
            )

            if self.current_pull_request:
                self.previous_notes = self.current_pull_request["body"]

        self.header = header
        self.list_item_symbol = list_item_symbol
        self.commit_codes_to_headings_mapping = (
            commit_codes_to_headings_mapping or COMMIT_CODES_TO_HEADINGS_MAPPING
        )
        self.include_link_to_pull_request = include_link_to_pull_request

        logger.info(f"Args: {self.__dict__}")
        logger.info(f"Using {self.stop_point!r} stop point.")

    def generate(self):
        """Generate a pull request description from the commit messages since the given stop point, sorting them into
        headed sections according to their commit codes via the commit-codes-to-headings mapping. If the previous set
        of release notes have been provided then:

        * If the skip indicator is present, the previous notes are returned as they are
        * Otherwise if the autogeneration indicators are present, the previous notes are left unchanged apart from
          between these indicators, where the new autogenerated release notes overwrite whatever was between them before
        * If the autogeneration indicators are not present, the new autogenerated release notes are added after the
          previous notes

        :return str:
        """
        if self.previous_notes and SKIP_INDICATOR in self.previous_notes:
            return self.previous_notes

        if self.current_pull_request:
            parsed_commits, unparsed_commits = self._parse_commit_messages_from_github()
        else:
            parsed_commits, unparsed_commits = (
                self._parse_commit_messages_from_git_log()
            )

        categorised_commit_messages, upgrade_instructions = (
            self._categorise_commit_messages(
                parsed_commits,
                unparsed_commits,
            )
        )

        autogenerated_release_notes = self._build_release_notes(
            categorised_commit_messages, upgrade_instructions
        )

        if not self.previous_notes:
            return autogenerated_release_notes

        previous_notes_before_generated_section = self.previous_notes.split(
            AUTO_GENERATION_START_INDICATOR
        )
        previous_notes_after_generated_section = "".join(
            previous_notes_before_generated_section[1:]
        ).split(AUTO_GENERATION_END_INDICATOR)

        return "\n".join(
            (
                previous_notes_before_generated_section[0].strip("\n"),
                autogenerated_release_notes,
                previous_notes_after_generated_section[-1].strip("\n"),
            )
        ).strip('"\n')

    def _get_current_pull_request(self, pull_request_url, api_token=None):
        """Get the current pull request from the GitHub API.

        :param str pull_request_url: the GitHub API URL for the pull request
        :param str|None api_token: GitHub API token
        :return dict|None:
        """
        if not api_token:
            headers = {}
        else:
            headers = {"Authorization": f"token {api_token}"}

        response = requests.get(pull_request_url, headers=headers)

        if response.status_code == 200:
            pull_request = response.json()
            pull_request["commits"] = self._get_pull_request_commits(
                pull_request, headers
            )
            return pull_request

        logger.warning(
            f"Pull request could not be accessed; resorting to using {LAST_RELEASE} stop point.\n"
            f"{response.status_code}: {response.text}."
        )

        self.stop_point = LAST_RELEASE
        return None

    def _get_pull_request_commits(self, pull_request, headers):
        """Get all the commits belonging to the pull request by requesting every page from the GitHub API.

        :param dict pull_request:
        :param dict headers:
        :return list:
        """
        commits = []

        response = requests.get(
            pull_request["commits_url"] + "?per_page=100", headers=headers
        )
        commits.extend(response.json())

        while "next" in response.links:
            response = requests.get(response.links["next"]["url"])
            commits.extend(response.json())

        return commits

    def _get_git_log(self):
        """Get the one-line decorated git log formatted in the pattern of "hash|§header|§body|§decoration@@@".

        Explanation:
        * "|§" delimits the hash from the header, the header from the potentially multi-line body, and the body from the
          decoration
        * "@@@" indicates the end of the git log line. "\n" cannot be used as commit bodies can contain newlines, so
        they can't be used by themselves to delimit git log entries.
        * The specific characters used for the delimiters have been chosen so that they are very uncommon to reduce
          delimiting errors

        :return list(str):
        """
        return (
            subprocess.run(
                ["git", "log", "--pretty=format:%h|§%s|§%b|§%d@@@"], capture_output=True
            )
            .stdout.strip()
            .decode()
        ).split("@@@")

    def _parse_commit_messages_from_git_log(self):
        """Parse commit messages from the git log (formatted using `--pretty=format:%h|§%s|§%b|§%d@@@`) until the stop
        point is reached. The parsed commit messages are returned separately to any that fail to parse.

        :return list(tuple), list(str):
        """
        parsed_commits = []
        unparsed_commits = []

        for commit in self._get_git_log():
            hash, header, body, decoration = commit.split("|§")

            if "tag" in decoration and bool(
                SEMANTIC_VERSION_PATTERN.search(decoration)
            ):
                break

            # Check if the commit message follows conventional commit format
            match = CONVENTIONAL_COMMIT_PATTERN.match(header)
            if not match:
                if not COMMIT_REF_MERGE_PATTERN.search(header):
                    unparsed_commits.append(header.strip())
                continue

            # Extract type and scope (if present)
            commit_type = match.group("type")
            scope = match.group("scope")
            # Get the rest of the message after the type(scope): prefix
            message = header[header.find(":") + 1 :].strip()

            parsed_commits.append(
                (commit_type.strip(), scope, message.strip(), body.strip())
            )

        return parsed_commits, unparsed_commits

    def _parse_commit_messages_from_github(self):
        """Parse commit messages from the GitHub pull request. The parsed commit messages are returned separately to
        any that fail to parse.

        :return list(tuple), list(str):
        """
        parsed_commits = []
        unparsed_commits = []

        for commit in self.current_pull_request["commits"]:
            header, *body = commit["commit"]["message"].split("\n")
            body = "\n".join(body)

            # Check if the commit message follows conventional commit format
            match = CONVENTIONAL_COMMIT_PATTERN.match(header)
            if not match:
                if not COMMIT_REF_MERGE_PATTERN.search(header):
                    unparsed_commits.append(header.strip())
                continue

            # Extract type and scope (if present)
            commit_type = match.group("type")
            scope = match.group("scope")
            # Get the rest of the message after the type(scope): prefix
            message = header[header.find(":") + 1 :].strip()

            parsed_commits.append(
                (commit_type.strip(), scope, message.strip(), body.strip())
            )

        return parsed_commits, unparsed_commits

    def _categorise_commit_messages(self, parsed_commits, unparsed_commits):
        """Categorise the commit messages into headed sections, with subgroups based on scope.
        Unparsed commits are put under an "uncategorised" header.
        Duplicate commit messages are removed (case-insensitive).

        :param iter(tuple) parsed_commits:
        :param iter(str) unparsed_commits:
        :return (dict, list): a mapping of section headers to a dict of scopes and their commits, and a list of breaking change commits
        """
        # Initialize with an empty dict for each heading instead of a list
        categorised_commits = {
            heading: {} for heading in self.commit_codes_to_headings_mapping.values()
        }
        categorised_commits[BREAKING_CHANGE_COUNT_KEY] = 0

        # Track lowercase versions of commit messages for case-insensitive duplicate detection
        commit_message_tracker = {
            heading: {} for heading in self.commit_codes_to_headings_mapping.values()
        }
        commit_message_tracker[OTHER_SECTION_HEADING] = {"Miscellaneous": set()}
        commit_message_tracker[UNCATEGORISED_SECTION_HEADING] = {"Miscellaneous": set()}

        breaking_change_upgrade_instructions = []

        for code, scope, header, body in parsed_commits:
            try:
                # Use "Miscellaneous" if no scope is provided
                effective_scope = scope if scope else "Miscellaneous"

                # Get the appropriate heading for this commit type
                heading = self.commit_codes_to_headings_mapping[code]

                # Initialize the scope dict if it doesn't exist
                if effective_scope not in categorised_commits[heading]:
                    categorised_commits[heading][effective_scope] = []
                    commit_message_tracker[heading][effective_scope] = set()

                if any(
                    indicator in body
                    for indicator in CONVENTIONAL_COMMIT_BREAKING_CHANGE_INDICATORS
                ):
                    commit_note = BREAKING_CHANGE_INDICATOR + header
                    categorised_commits[BREAKING_CHANGE_COUNT_KEY] += 1

                    # Remove the breaking change indicator from the body and put the body in a collapsible section
                    # under the commit header.
                    upgrade_instruction = ":".join(body.split(":")[1:]).strip()

                    breaking_change_upgrade_instructions.append(
                        "<details>\n"
                        f"<summary>💥 <b>({effective_scope}) {header}</b></summary>\n"
                        f"\n{upgrade_instruction}\n"
                        "</details>"
                    )
                else:
                    commit_note = header

                # Case-insensitive duplicate check
                lowercase_note = commit_note.lower()
                if (
                    lowercase_note
                    not in commit_message_tracker[heading][effective_scope]
                ):
                    categorised_commits[heading][effective_scope].append(commit_note)
                    commit_message_tracker[heading][effective_scope].add(lowercase_note)

            except KeyError:
                # For commits with unknown types, add them to the OTHER section
                if "Miscellaneous" not in categorised_commits[OTHER_SECTION_HEADING]:
                    categorised_commits[OTHER_SECTION_HEADING]["Miscellaneous"] = []
                    commit_message_tracker[OTHER_SECTION_HEADING]["Miscellaneous"] = (
                        set()
                    )

                # Case-insensitive duplicate check
                lowercase_header = header.lower()
                if (
                    lowercase_header
                    not in commit_message_tracker[OTHER_SECTION_HEADING][
                        "Miscellaneous"
                    ]
                ):
                    categorised_commits[OTHER_SECTION_HEADING]["Miscellaneous"].append(
                        header
                    )
                    commit_message_tracker[OTHER_SECTION_HEADING]["Miscellaneous"].add(
                        lowercase_header
                    )

        try:
            # Handle uncategorized commits (with case-insensitive duplicate removal)
            if (
                "Miscellaneous"
                not in categorised_commits[UNCATEGORISED_SECTION_HEADING]
            ):
                categorised_commits[UNCATEGORISED_SECTION_HEADING]["Miscellaneous"] = []
                commit_message_tracker[UNCATEGORISED_SECTION_HEADING][
                    "Miscellaneous"
                ] = set()

            for commit in unparsed_commits:
                lowercase_commit = commit.lower()
                if (
                    lowercase_commit
                    not in commit_message_tracker[UNCATEGORISED_SECTION_HEADING][
                        "Miscellaneous"
                    ]
                ):
                    categorised_commits[UNCATEGORISED_SECTION_HEADING][
                        "Miscellaneous"
                    ].append(commit)
                    commit_message_tracker[UNCATEGORISED_SECTION_HEADING][
                        "Miscellaneous"
                    ].add(lowercase_commit)
        except KeyError:
            logger.warning(
                "Uncategorised commits could not be added to the release notes."
            )

        return categorised_commits, breaking_change_upgrade_instructions

    def _build_release_notes(self, categorised_commit_messages, upgrade_instructions):
        """Build the the categorised commit messages into a single multi-line string ready to be used as formatted
        release notes.

        :param dict categorised_commit_messages:
        :param list(str) upgrade_instructions: an upgrade instruction for each breaking change
        :return str:
        """
        breaking_change_count = categorised_commit_messages.pop(
            BREAKING_CHANGE_COUNT_KEY
        )
        upgrade_instructions_section = ""

        contents_section = self._create_contents_section(
            categorised_commit_messages, breaking_change_count
        )

        if breaking_change_count > 0:
            upgrade_instructions_section = (
                "---\n"
                + self._create_breaking_change_upgrade_section(upgrade_instructions)
            )

        return "".join(
            [
                f"{AUTO_GENERATION_START_INDICATOR}\n",
                contents_section,
                upgrade_instructions_section,
                AUTO_GENERATION_END_INDICATOR,
            ]
        )

    def _create_contents_section(
        self, categorised_commit_messages, breaking_change_count
    ):
        """Create the contents section of the release notes.

        :param dict categorised_commit_messages:
        :param int breaking_change_count: the number of breaking changes
        :return str:
        """
        # if self.current_pull_request is not None and self.include_link_to_pull_request:
        #     link_to_pull_request = (
        #         f" ([#{self.current_pull_request['number']}]({self.current_pull_request['html_url']}))"
        #     )
        # else:
        #     link_to_pull_request = ""

        contents_section = ""

        ticket_re = re.compile(r"[a-zA-Z]{2,6}-\d+")
        tickets = []

        for heading, scoped_notes in categorised_commit_messages.items():
            for _, notes in sorted(scoped_notes.items()):
                logger.warning(f"Notes: {notes}")
                if not notes:
                    continue
                for note in notes:
                    matches = ticket_re.findall(note)
                    for match in matches:
                        tickets.append(match)

        
        logger.warning(f"Tickets: {tickets}")

        # contents_section += f"{self.header}\n\n"
        
        if tickets:
            contents_section += "# Tickets\n"
            # Dedup keys maintaining insertion order using dict.fromkeys(tickets).keys() instead of set(tickets)
            contents_section += "\n".join(
                self.list_item_symbol + " " + note
                for note in dict.fromkeys(tickets).keys()
            )

        if breaking_change_count:
            contents_section += self._create_breaking_change_warning(
                breaking_change_count
            )

        # Process regular sections first (excluding OTHER and UNCATEGORISED)
        for heading, scoped_notes in categorised_commit_messages.items():
            # Skip special sections and empty sections
            if (
                heading
                in {
                    OTHER_SECTION_HEADING,
                    UNCATEGORISED_SECTION_HEADING,
                    BREAKING_CHANGE_COUNT_KEY,
                }
                or not scoped_notes
                or not any(notes for scope, notes in scoped_notes.items())
            ):
                continue

            contents_section += self._create_contents_subsection(
                heading=heading, scoped_notes=scoped_notes
            )

        # Process OTHER and UNCATEGORISED sections last, but only if they have content
        for heading in (OTHER_SECTION_HEADING, UNCATEGORISED_SECTION_HEADING):
            scoped_notes = categorised_commit_messages.get(heading, {})

            # Check if there are any actual notes in any of the scopes
            if scoped_notes and any(notes for scope, notes in scoped_notes.items()):
                contents_section += self._create_contents_subsection(
                    heading=heading, scoped_notes=scoped_notes
                )

        return contents_section

    def _create_breaking_change_warning(self, breaking_change_count):
        """Create a breaking change warning string.

        :param int breaking_change_count: The number of breaking changes
        :return str:
        """
        if breaking_change_count == 1:
            return (
                f"**IMPORTANT:** There is {breaking_change_count} breaking change.\n\n"
            )

        return f"**IMPORTANT:** There are {breaking_change_count} breaking changes.\n\n"

    def _create_contents_subsection(self, heading, scoped_notes):
        """Create a section of the release notes with the given heading followed by scoped subsections
        containing the notes formatted into bulleted lists.

        :param str heading:
        :param dict scoped_notes: A dictionary mapping scopes to lists of notes
        :return str:
        """
        subsection = f"{heading}\n"

        for scope, notes in sorted(scoped_notes.items()):
            if not notes:
                continue

            # Add a subheading for the scope
            formatted_scope = re.sub(r"[-_]+", " ", scope).title()
            subsection += f"### {formatted_scope}\n"

            # Add the bulleted list of notes under this scope
            note_lines = "\n".join(
                " - " + (note[:1].upper() + note[1:])
                for note in notes
            )
            subsection += f"{note_lines}\n\n"

        return subsection

    def _create_breaking_change_upgrade_section(self, upgrade_instructions):
        """Create an upgrade section explaining how to update to deal with breaking changes.

        :param list(str) upgrade_instructions: an upgrade instruction for each breaking change (can be any amount of markdown)
        :return str: breaking change upgrade_section
        """
        return (
            "\n".join(
                [
                    UPGRADE_INSTRUCTIONS_HEADER,
                    "\n\n".join(upgrade_instructions),
                ]
            )
            + "\n\n"
        )


def main(argv=None):
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "stop_point",
        choices=STOP_POINTS,
        help="The point in the git history to stop compiling commits into the pull request description.",
    )

    parser.add_argument(
        "--pull-request-url",
        default=None,
        type=str,
        help="Provide the API URL of a pull request (e.g. https://api.github.com/repos/octue/conventional-commits/pulls/1) "
        "if you want to update a pull request's description with the generated release notes. It must be provided "
        "alongside --api-token if the repository is private.",
    )

    parser.add_argument(
        "--api-token",
        default=None,
        type=str,
        help="A valid GitHub API token for the repository the pull request belongs to. There is no need to provide this if the repository is public.",
    )

    parser.add_argument(
        "--header",
        default="# Contents",
        type=str,
        help="The header (including MarkDown styling) to put the release notes under. Default is '# Contents'",
    )

    parser.add_argument(
        "--list-item-symbol",
        default="-",
        help="The MarkDown list item symbol to use for listing commit messages in the release notes. Default is '- '",
    )

    parser.add_argument(
        "--no-link-to-pull-request",
        action="store_true",
        help="If provided, don't add a link to the given pull request in the release notes.",
    )

    args = parser.parse_args(argv)

    release_notes = PullRequestDescriptionGenerator(
        stop_point=args.stop_point,
        pull_request_url=args.pull_request_url,
        api_token=args.api_token,
        header=args.header,
        list_item_symbol=args.list_item_symbol,
        include_link_to_pull_request=not args.no_link_to_pull_request,
    ).generate()

    print(release_notes)


if __name__ == "__main__":
    main()
