# Runbook to update splunk/appinspect-cli-action

Once Splunk AppInspect team releases AppInspect CLI - we need to make sure that everyone runs the latest version.

## Steps

### Merge splunk/appinspect-cli-action PR

- go to the PR [example](https://github.com/splunk/appinspect-cli-action/pull/127)
    - check [release notes](https://dev.splunk.com/enterprise/docs/relnotes/relnotes-appinspectcli/whatsnew/) for the new version add release notes link in the PR comment
    - make sure that the pipeline is green
        - if not - investigate why and report and issue to the AppInspect team
    - determine which version of `appinspect-cli-action` needs to be released based on the [splunk-appinspect release](https://dev.splunk.com/enterprise/docs/relnotes/relnotes-appinspectcli/whatsnew/)
        - if it is a bug fix release (E.g. v3.6.0 > v3.6.1) - change "chore" to "fix" in the title of the PR and skip [update section](#update-splunkaddonfactory-workflow-addon-release)
        - if it is a minor release (E.g v3.6.0 > v3.7.0) - change "chore" to "feat" in the title of the PR
    - get review from the team
    - "Squash and merge" the PR
    - wait for the release
        - make sure that the proper version of the action is released

### Update splunk/addonfactory-workflow-addon-release

- create a PR in this repository with a new version of the action ([example PR](https://github.com/splunk/addonfactory-workflow-addon-release/pull/247))
    - make sure that PR is towards `main` branch
    - make sure the title of the PR follows the format: "fix: update AppInspect CLI action to v.X.Y"
    - make sure that the pipeline is green
    - attach a link to a test run of reusable workflow
    - get review from the team
    - "Squash and merge" the PR
- backport this change to develop right away ([example PR](https://github.com/splunk/addonfactory-workflow-addon-release/pull/239))
    - create a PR from `main` to `develop`
    - get review from the team
    - "Merge pull request" the PR

### Notify teams

- notify development teams in #ta-engineering-dev-all Slack channel (look for the previous announcements to see the format)
