# addonfactory-workflow-addon-release
Repository to store reusable `build-test-release` workflow, which is used to release Splunk add-ons. 
Worklow is used by add-ons created and managed by [addonfactory repository template](https://github.com/splunk/addonfactory-repository-template)
Workflow defines jobs which perform security code scanning, execute different types of tests, build add-on package, make github release.

## Example usage
```yaml
name: build-test-release
on:
  push:
    branches:
      - "main"
    tags:
      - "v[0-9]+.[0-9]+.[0-9]+"
  pull_request:
    branches: 
      - "**"

jobs:
  call-workflow:
    uses: splunk/addonfactory-workflow-addon-release/.github/workflows/reusable-build-test-release.yml@v1.2.0
    secrets:
      GH_TOKEN_ADMIN: ${{ secrets.GH_TOKEN_ADMIN }}
      GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
      SEMGREP_PUBLISH_TOKEN: ${{ secrets.SEMGREP_PUBLISH_TOKEN }}
      AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
      AWS_DEFAULT_REGION: ${{ secrets.AWS_DEFAULT_REGION }}
      AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
      VT_API_KEY: ${{ secrets.VT_API_KEY }}
      CODECOV_TOKEN: ${{ secrets.CODECOV_TOKEN }}
      OTHER_TA_REQUIRED_CONFIGS: ${{ secrets.OTHER_TA_REQUIRED_CONFIGS }}
```
