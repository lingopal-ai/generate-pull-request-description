# Conventional Commits Changelog Generator

A GitHub action that generates structured changelogs from [Conventional Commit messages](https://www.conventionalcommits.org/en/v1.0.0/) in your pull requests. Built upon the excellent work by [octue/generate-pull-request-description](https://github.com/octue/generate-pull-request-description), this fork adds several improvements:

- Support for latest Conventional Commits standard
- Added support for commit scopes (e.g. `feat(api): new endpoint`)
- Enhanced section headings with emojis
- Improved categorization of commits

## Features summary

- Automatic breaking change highlighting and upgrade instructions
- Automatic categorization of all commit messages in the pull request branch
- Choosing which part of the description to generate, enabling descriptions containing a generated section alongside
  static/user-written sections
- Easy skipping of description updating when you're ready to fine-tune and edit the description

## Usage

Add the action to pull request workflows alongside the
[`riskledger/update-pr-description`](https://github.com/riskledger/update-pr-description) action to dynamically update
the description each time you push:

```yaml
steps:
- uses: actions/checkout@v3

- uses: tanmaypathak/generate-pull-request-description@1.0.0.beta-2
  id: pr-description
  with:
    pull_request_url: ${{ github.event.pull_request.url }}
    api_token: ${{ secrets.GITHUB_TOKEN }}

- name: Update pull request body
  uses: riskledger/update-pr-description@v2
  with:
    body: ${{ steps.pr-description.outputs.pull_request_description }}
    token: ${{ secrets.GITHUB_TOKEN }}
```

## Features

### Breaking change highlighting

If breaking changes are indicated in any of the commit messages' bodies via `BREAKING CHANGE:` or `BREAKING-CHANGE:`,
these commits are marked and a warning is added to the top of the release notes. Any other text in these sections is
treated as upgrade instructions and added to the end of the pull request description.

### Choosing which section of the description to update

The only part of the pull request description that is updated by the action is the section bounded by:

- `<!--- START AUTOGENERATED NOTES --->`
- `<!--- END AUTOGENERATED NOTES --->`

This section will always be replaced with a summary of all the commit messages in the pull request so far. The markers
(invisible in rendered markdown) are added in automatically when the action first runs, but you can move them manually.
Any text you add outside of these will remain untouched so you can add, for example, your own static summary of the
pull request while having a dynamic summary of the commits.

### Skipping description updates

Updating the description is skipped if the `<!--- SKIP AUTOGENERATED NOTES --->` marker is present anywhere in the pull
request description. This allows commits to be documented in the description until you're ready to fine-tune or
cherry-pick commit descriptions when your pull request is ready for review.
