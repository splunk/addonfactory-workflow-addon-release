# Runbook to creating and publishing docker images used in reusable workflow
## Runbook to publish multiple images of different Linux flavors and versions for scripted inputs tests
Once there is new Splunk release, and [matrix](https://github.com/splunk/addonfactory-test-matrix-action) is updated, we need to make sure that Splunk images for scripted inputs tests are created and published.
### Steps

#### Update OS images
- check what OS are listed in definition of matrix in scripted inputs tests [here](https://github.com/splunk/addonfactory-workflow-addon-release/blob/v4.16/.github/workflows/reusable-build-test-release.yml#L1966)
- if any is missing in [ta-automation-docker-images](https://cd.splunkdev.com/taautomation/ta-automation-docker-images/-/tree/main/dockerfiles) then add new Dockerfile

#### Create images and publish them to ECR
- figure out what version of Splunk is needed (sha) using go/fetcher
- trigger [pipeline](https://cd.splunkdev.com/taautomation/ta-automation-docker-images/-/pipelines/new) for every OS flavor separately

## Runbook to publish unreleased splunk image for testing
Whenever there is a need for running tests with unreleased splunk, we need to create relevant splunk docker image and publish it to aws ecr
### Steps
#### Build docker image and publish to artifactory
- Prior creating docker image it needs to be determined which revision of core splunk repo is required. Splunk docker images are based on splunk builds published to artifactory by CI in core repository. Their names match SHA of the commit in core repo: [develop builds artifactory](https://repo.splunkdev.net:443/artifactory/generic/splcore/builds/develop/)
- Docker image is built by [pipeline](https://cd.splunkdev.com/core-ee/docker-splunk-internal/-/pipelines/new) which required UNRELEASED_SPLUNK_SHA as an input variable - provide first 12 characters of desired revision on splunk core repo. Once image is built, it is published to [artifactory](https://repo.splunkdev.net/ui/repos/tree/General/docker/docker-splunk-internal/unreleased/splunk-redhat-9).
#### Pull built image locally, tag and publish to ecr
- docker pull docker.repo.splunkdev.net/docker-splunk-internal/unreleased/splunk-redhat-9:[image-tag]
- docker tag docker.repo.splunkdev.net/docker-splunk-internal/unreleased/splunk-redhat-9:[image-tag] "956110764581.dkr.ecr.us-west-2.amazonaws.com/splunk/splunk:[new-image-tag]"
- set AWS environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN
- aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin 956110764581.dkr.ecr.us-west-2.amazonaws.com
- docker push 956110764581.dkr.ecr.us-west-2.amazonaws.com/splunk/splunk:[new-image-tag]
- confirm that image is visible in AWS [ECR](https://us-west-2.console.aws.amazon.com/ecr/repositories/private/956110764581/splunk/splunk?region=us-west-2) 