# Runbook to publish multiple images of different linux flavours and versions for scripted inputs tests

Once there is new Splunk release, and [matrix](https://github.com/splunk/addonfactory-test-matrix-action) is updated, we need to make sure that splunk images for scripted inputs tests are created and published.
## Steps

### Update OS images
- check what OS are listed in definition of matrix in scripted inputs tests [here](https://github.com/splunk/addonfactory-workflow-addon-release/blob/v4.16/.github/workflows/reusable-build-test-release.yml#L1966)
- if any is missing in [ta-automation-docker-images](https://cd.splunkdev.com/taautomation/ta-automation-docker-images/-/tree/main/dockerfiles) then add new dockerfile

### Create images and publish them to ECR
- figure out what version of Splunk is needed (sha) using go/fetcher
- trigger [pipeline](https://cd.splunkdev.com/taautomation/ta-automation-docker-images/-/pipelines/new) for every os flavour separately
