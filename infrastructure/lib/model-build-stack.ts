import * as path from 'node:path';
import { Construct } from 'constructs';
import {
  CfnOutput,
  Duration,
  RemovalPolicy,
  Stack,
  StackProps,
  Tags,
  aws_codebuild as codebuild,
  aws_ecr as ecr,
  aws_logs as logs,
  aws_s3_assets as assets,
} from 'aws-cdk-lib';

export class ModelBuildStack extends Stack {
  constructor(scope: Construct, id: string, props: StackProps) {
    super(scope, id, props);
    const repository = new ecr.Repository(this, 'DeciderImages', {
      imageScanOnPush: true,
      imageTagMutability: ecr.TagMutability.IMMUTABLE,
      removalPolicy: RemovalPolicy.RETAIN,
    });
    const source = new assets.Asset(this, 'InferenceSource', {
      path: path.resolve(__dirname, '../../backend/decider'),
    });
    const logGroup = new logs.LogGroup(this, 'BuildLogs', {
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    const project = new codebuild.Project(this, 'BuildDecider', {
      source: codebuild.Source.s3({ bucket: source.bucket, path: source.s3ObjectKey }),
      environment: {
        buildImage: codebuild.LinuxBuildImage.STANDARD_7_0,
        computeType: codebuild.ComputeType.LARGE,
        privileged: true,
      },
      timeout: Duration.minutes(45),
      concurrentBuildLimit: 1,
      logging: { cloudWatch: { logGroup } },
      environmentVariables: { IMAGE_REPOSITORY: { value: repository.repositoryUri } },
      buildSpec: codebuild.BuildSpec.fromObject({
        version: '0.2',
        phases: {
          pre_build: {
            commands: [
              'aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "${IMAGE_REPOSITORY%/*}"',
              'export IMAGE_TAG="build-${CODEBUILD_BUILD_NUMBER}"',
            ],
          },
          build: {
            commands: ['docker build --provenance=false -t "$IMAGE_REPOSITORY:$IMAGE_TAG" .'],
          },
          post_build: {
            commands: [
              'test "$CODEBUILD_BUILD_SUCCEEDING" = "1"',
              'docker push "$IMAGE_REPOSITORY:$IMAGE_TAG"',
            ],
          },
        },
      }),
    });
    repository.grantPullPush(project);
    Tags.of(repository).add('Component', 'inference');
    Tags.of(project).add('Component', 'build');
    new CfnOutput(this, 'BuildProjectName', { value: project.projectName });
    new CfnOutput(this, 'ImageRepositoryUri', { value: repository.repositoryUri });
    new CfnOutput(this, 'ImageRepositoryName', { value: repository.repositoryName });
  }
}
