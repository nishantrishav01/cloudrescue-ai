import * as cdk from 'aws-cdk-lib';
import { CloudRescueStack, IncidentDataStack } from '../lib/cloudrescue-stack';
import { ModelBuildStack } from '../lib/model-build-stack';

const app = new cdk.App();
const environment = String(app.node.tryGetContext('environment') ?? 'dev');
if (!/^[a-z][a-z0-9-]{1,15}$/.test(environment)) throw new Error('Invalid environment name.');
const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION ?? process.env.AWS_REGION ?? 'ap-south-1',
};
const tags = {
  Project: 'CloudRescueAI',
  Application: 'cloudrescue-ai',
  Environment: environment,
  Owner: String(app.node.tryGetContext('owner') ?? 'cloudrescue-team'),
  CostCenter: String(app.node.tryGetContext('costCenter') ?? 'hackathon'),
  ManagedBy: 'AWS-CDK',
  DataClassification: 'Internal',
};
for (const [key, value] of Object.entries(tags)) cdk.Tags.of(app).add(key, value);
const data = new IncidentDataStack(app, `CloudRescue-${environment}-Data`, {
  env,
  terminationProtection: true,
  description: 'CloudRescue AI encrypted incident persistence',
  tags,
});
new CloudRescueStack(app, `CloudRescue-${environment}`, {
  env,
  environment,
  incidentTable: data.incidentTable,
  description: 'CloudRescue AI authenticated incident simulation war room',
  tags,
});
new ModelBuildStack(app, `CloudRescue-${environment}-ModelBuild`, {
  env,
  description: 'On-demand CPU Decider image build; no local Docker required',
  tags,
});
