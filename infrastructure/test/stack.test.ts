import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { App } from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { CloudRescueStack, IncidentDataStack } from '../lib/cloudrescue-stack';
import { DeciderFargate } from '../lib/decider-fargate';
import { Stack, aws_ecr as ecr } from 'aws-cdk-lib';

test('storage is encrypted, retained and protected', () => {
  const app = new App();
  const stack = new IncidentDataStack(app, 'Data', {});
  const template = Template.fromStack(stack);
  template.hasResourceProperties('AWS::DynamoDB::Table', {
    BillingMode: 'PAY_PER_REQUEST',
    DeletionProtectionEnabled: true,
    SSESpecification: { SSEEnabled: true },
  });
  template.hasResource('AWS::DynamoDB::Table', { DeletionPolicy: 'Retain' });
});

test('API is protected and simulator has no infrastructure mutation actions', () => {
  const app = new App();
  const data = new IncidentDataStack(app, 'Data', {});
  const assets = fs.mkdtempSync(path.join(os.tmpdir(), 'cloudrescue-assets-'));
  fs.writeFileSync(path.join(assets, 'index.html'), '<html></html>');
  const stack = new CloudRescueStack(app, 'App', {
    environment: 'test',
    incidentTable: data.incidentTable,
    assetPath: assets,
    frontendPath: assets,
  });
  const template = Template.fromStack(stack);
  template.hasResourceProperties('AWS::ApiGatewayV2::Route', {
    RouteKey: 'GET /api/{proxy+}',
    AuthorizationType: 'JWT',
  });
  template.hasResourceProperties('AWS::S3::Bucket', {
    PublicAccessBlockConfiguration: {
      BlockPublicAcls: true,
      BlockPublicPolicy: true,
      IgnorePublicAcls: true,
      RestrictPublicBuckets: true,
    },
  });
  template.hasResourceProperties('AWS::StepFunctions::StateMachine', {
    StateMachineType: 'STANDARD',
  });
  template.hasResourceProperties('AWS::Cognito::UserPool', {
    AdminCreateUserConfig: { AllowAdminCreateUserOnly: true },
  });
  template.hasResourceProperties('AWS::IAM::Policy', {
    PolicyDocument: {
      Statement: Match.arrayWith([Match.objectLike({ Action: 'bedrock:InvokeModel' })]),
    },
  });
  const serialized = JSON.stringify(template.toJSON());
  template.hasResourceProperties('AWS::Lambda::Function', {
    Handler: 'backend.live.discovery.handler',
    Timeout: 180,
  });
  template.hasResourceProperties('AWS::Cognito::UserPoolGroup', { GroupName: 'RecoveryApprovers' });
  for (const forbidden of [
    'rds:Failover',
    'ec2:Modify',
    'autoscaling:Update',
    'iam:PassRole',
    'backup:StartRestoreJob',
  ])
    assert.equal(serialized.includes(forbidden), false);
});

test('a pinned Decider image can be synthesized with isolated inference memory', () => {
  const app = new App({
    context: {
      enableDecider: true,
      deciderImageUri: `123456789012.dkr.ecr.ap-south-1.amazonaws.com/decider@sha256:${'a'.repeat(64)}`,
    },
  });
  const data = new IncidentDataStack(app, 'Data', {});
  const assets = fs.mkdtempSync(path.join(os.tmpdir(), 'cloudrescue-model-assets-'));
  fs.writeFileSync(path.join(assets, 'handler.py'), '');
  const stack = new CloudRescueStack(app, 'App', {
    environment: 'test',
    incidentTable: data.incidentTable,
    assetPath: assets,
    frontendPath: assets,
  });
  const template = Template.fromStack(stack);
  template.hasResourceProperties('AWS::Lambda::Function', {
    PackageType: 'Image',
    MemorySize: 10240,
    Timeout: 840,
    Environment: {
      Variables: Match.objectLike({ HF_HUB_OFFLINE: '1', TRANSFORMERS_OFFLINE: '1' }),
    },
  });
});

test('Fargate inference has no running service or public inbound rule', () => {
  const app = new App();
  const stack = new Stack(app, 'Inference', {
    env: { account: '123456789012', region: 'ap-south-1' },
  });
  const repository = ecr.Repository.fromRepositoryName(stack, 'Image', 'decider');
  new DeciderFargate(stack, 'Decider', { repository, digest: `sha256:${'a'.repeat(64)}` });
  const template = Template.fromStack(stack);
  template.resourceCountIs('AWS::ECS::Service', 0);
  template.resourceCountIs('AWS::EC2::NatGateway', 0);
  template.resourceCountIs('AWS::EC2::SecurityGroupIngress', 0);
  template.hasResourceProperties('AWS::ECS::TaskDefinition', {
    Cpu: '4096',
    Memory: '12288',
    NetworkMode: 'awsvpc',
    RequiresCompatibilities: ['FARGATE'],
  });
});
