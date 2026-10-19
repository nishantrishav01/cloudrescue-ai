import * as fs from 'node:fs';
import * as path from 'node:path';
import { Construct } from 'constructs';
import {
  CfnOutput,
  Duration,
  RemovalPolicy,
  Stack,
  Tags,
  Token,
  aws_cloudwatch as cloudwatch,
  aws_cloudwatch_actions as actions,
  aws_cognito as cognito,
  aws_dynamodb as dynamodb,
  aws_events as events,
  aws_events_targets as targets,
  aws_iam as iam,
  aws_lambda as lambda,
  aws_lambda_destinations as destinations,
  aws_logs as logs,
  aws_s3 as s3,
  aws_s3_assets as assets,
  aws_sns as sns,
  aws_sqs as sqs,
  aws_stepfunctions as sfn,
  aws_stepfunctions_tasks as tasks,
} from 'aws-cdk-lib';

interface RecoveryTarget {
  id: string;
  resource_arn: string;
  resource_type: 'EC2' | 'RDS';
  dr_region: string;
  backup_vault_name: string;
  restore_role_arn: string;
  restore_metadata: Record<string, string>;
}

export class LiveRecovery extends Construct {
  constructor(
    scope: Construct,
    id: string,
    props: {
      code: lambda.Code;
      api: lambda.Function;
      agent: lambda.Function;
      table: dynamodb.ITable;
      userPool: cognito.IUserPool;
    },
  ) {
    super(scope, id);
    const stack = Stack.of(this);
    const localConfigPath = path.resolve(__dirname, '../recovery-targets.json');
    const configPath = fs.existsSync(localConfigPath)
      ? localConfigPath
      : path.resolve(__dirname, '../recovery-targets.example.json');
    const config = JSON.parse(fs.readFileSync(configPath, 'utf8')) as {
      enabled: boolean;
      policy: {
        account_id: string;
        primary_region: string;
        dr_region: string;
        rto_seconds: number;
        rpo_seconds: number;
        approval_ttl_seconds: number;
        require_different_approver: boolean;
        automatic_cutover: boolean;
        approver_group: string;
      };
      targets: RecoveryTarget[];
    };
    if (
      typeof config.enabled !== 'boolean' ||
      (!Token.isUnresolved(stack.account) && config.policy.account_id !== stack.account) ||
      config.policy.require_different_approver !== true ||
      config.policy.automatic_cutover !== false ||
      config.policy.approver_group !== 'RecoveryApprovers' ||
      !Number.isInteger(config.policy.rto_seconds) ||
      config.policy.rto_seconds < 60 ||
      config.policy.rto_seconds > 86400 ||
      !Number.isInteger(config.policy.rpo_seconds) ||
      config.policy.rpo_seconds < 60 ||
      config.policy.approval_ttl_seconds !== 600 ||
      (config.enabled && !config.targets.length)
    )
      throw new Error('Invalid recovery scope or approval configuration.');
    if (
      new Set(config.targets.map((target) => target.id)).size !== config.targets.length ||
      new Set(config.targets.map((target) => target.resource_arn)).size !== config.targets.length
    )
      throw new Error('Recovery target IDs and source resources must be unique.');
    for (const target of config.targets) {
      if (
        !/^[a-z][a-z0-9-]{1,50}$/.test(target.id) ||
        !['EC2', 'RDS'].includes(target.resource_type) ||
        target.dr_region !== config.policy.dr_region ||
        !target.resource_arn.startsWith(
          `arn:aws:${target.resource_type === 'EC2' ? 'ec2' : 'rds'}:${config.policy.primary_region}:${config.policy.account_id}:`,
        ) ||
        !target.restore_role_arn.startsWith(`arn:aws:iam::${config.policy.account_id}:role/`) ||
        !/^[A-Za-z0-9_-]{2,50}$/.test(target.backup_vault_name)
      )
        throw new Error(
          'Recovery target is outside the configured account, region or supported runbook.',
        );
    }
    const manifest = new assets.Asset(this, 'RecoveryConfiguration', { path: configPath });
    const inventory = new s3.Bucket(this, 'Inventory', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: RemovalPolicy.RETAIN,
    });
    const deadLetters = new sqs.Queue(this, 'Failures', {
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      enforceSSL: true,
      retentionPeriod: Duration.days(14),
    });
    const alerts = new sns.Topic(this, 'OperationalAlerts', { enforceSSL: true });
    const makeWorker = (name: string, handler: string, timeout: number) => {
      const logGroup = new logs.LogGroup(this, `${name}Logs`, {
        retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: RemovalPolicy.DESTROY,
      });
      const worker = new lambda.Function(this, name, {
        code: props.code,
        runtime: lambda.Runtime.PYTHON_3_12,
        architecture: lambda.Architecture.X86_64,
        handler,
        timeout: Duration.seconds(timeout),
        memorySize: 512,
        logGroup,
        environment: { AWS_ACCOUNT_ID: stack.account },
        tracing: lambda.Tracing.ACTIVE,
      });
      new cloudwatch.Alarm(this, `${name}Errors`, {
        metric: worker.metricErrors({ period: Duration.minutes(5) }),
        threshold: 1,
        evaluationPeriods: 1,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      }).addAlarmAction(new actions.SnsAction(alerts));
      return worker;
    };
    const discovery = makeWorker('Discovery', 'backend.live.discovery.handler', 180);
    discovery.addEnvironment('LIVE_BUCKET', inventory.bucketName);
    inventory.grantPut(discovery, 'inventory/*');
    discovery.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          'ec2:DescribeInstances',
          'rds:DescribeDBInstances',
          'lambda:ListFunctions',
          'cloudwatch:GetMetricData',
          'cloudwatch:DescribeAlarms',
        ],
        resources: ['*'],
        conditions: { StringEquals: { 'aws:RequestedRegion': stack.region } },
      }),
    );
    discovery.addToRolePolicy(
      new iam.PolicyStatement({ actions: ['s3:ListAllMyBuckets'], resources: ['*'] }),
    );
    discovery.configureAsyncInvoke({
      maxEventAge: Duration.minutes(5),
      retryAttempts: 1,
      onFailure: new destinations.SqsDestination(deadLetters),
    });
    new events.Rule(this, 'DiscoverySchedule', {
      schedule: events.Schedule.rate(Duration.minutes(5)),
      targets: [
        new targets.LambdaFunction(discovery, { deadLetterQueue: deadLetters, retryAttempts: 1 }),
      ],
    });
    for (const fn of [props.api, props.agent]) {
      fn.addEnvironment('AWS_ACCOUNT_ID', stack.account);
      fn.addEnvironment('LIVE_BUCKET', inventory.bucketName);
      inventory.grantRead(fn, 'inventory/latest.json');
    }
    discovery.grantInvoke(props.api);
    props.api.addEnvironment('DISCOVERY_FUNCTION', discovery.functionName);
    const executor = makeWorker('RestoreExecutor', 'backend.recovery.handler.handler', 60);
    executor.addEnvironment('INCIDENT_TABLE', props.table.tableName);
    executor.addEnvironment('ALERTS_TOPIC_ARN', alerts.topicArn);
    alerts.grantPublish(executor);
    new cloudwatch.Alarm(this, 'QueuedFailures', {
      metric: deadLetters.metricApproximateNumberOfMessagesVisible({ period: Duration.minutes(5) }),
      threshold: 1,
      evaluationPeriods: 1,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    }).addAlarmAction(new actions.SnsAction(alerts));
    props.table.grantReadWriteData(executor);
    for (const fn of [props.api, executor]) {
      fn.addEnvironment('RECOVERY_CONFIG_BUCKET', manifest.s3BucketName);
      fn.addEnvironment('RECOVERY_CONFIG_KEY', manifest.s3ObjectKey);
      manifest.bucket.grantRead(fn, manifest.s3ObjectKey);
      if (config.targets.length) {
        fn.addToRolePolicy(
          new iam.PolicyStatement({
            actions: ['backup:ListRecoveryPointsByBackupVault'],
            resources: [
              ...new Set(
                config.targets.map(
                  (target) =>
                    `arn:${stack.partition}:backup:${target.dr_region}:${stack.account}:backup-vault:${target.backup_vault_name}`,
                ),
              ),
            ],
          }),
        );
        fn.addToRolePolicy(
          new iam.PolicyStatement({
            actions: ['backup:DescribeRecoveryPoint'],
            resources: [
              `arn:${stack.partition}:backup:${config.policy.dr_region}:${stack.account}:recovery-point:*`,
              `arn:${stack.partition}:rds:${config.policy.dr_region}:${stack.account}:snapshot:*`,
            ],
          }),
        );
        fn.addToRolePolicy(
          new iam.PolicyStatement({
            actions: [
              'ec2:DescribeSubnets',
              'ec2:DescribeSecurityGroups',
              'rds:DescribeDBSubnetGroups',
            ],
            resources: ['*'],
            conditions: { StringEquals: { 'aws:RequestedRegion': config.policy.dr_region } },
          }),
        );
      }
    }
    if (config.enabled) {
      executor.addToRolePolicy(
        new iam.PolicyStatement({
          actions: ['backup:StartRestoreJob'],
          resources: [
            `arn:${stack.partition}:backup:${config.policy.dr_region}:${stack.account}:recovery-point:*`,
            `arn:${stack.partition}:rds:${config.policy.dr_region}:${stack.account}:snapshot:*`,
          ],
          conditions: { StringEquals: { 'aws:RequestedRegion': config.policy.dr_region } },
        }),
      );
      executor.addToRolePolicy(
        new iam.PolicyStatement({
          actions: ['backup:DescribeRestoreJob'],
          resources: ['*'],
          conditions: { StringEquals: { 'aws:RequestedRegion': config.policy.dr_region } },
        }),
      );
      executor.addToRolePolicy(
        new iam.PolicyStatement({
          actions: ['iam:PassRole'],
          resources: [...new Set(config.targets.map((target) => target.restore_role_arn))],
          conditions: { StringEquals: { 'iam:PassedToService': 'backup.amazonaws.com' } },
        }),
      );
    }
    const failed = new tasks.LambdaInvoke(this, 'RecordRestoreFailure', {
      lambdaFunction: executor,
      payload: sfn.TaskInput.fromObject({
        plan_id: sfn.JsonPath.stringAt('$.plan_id'),
        failure: true,
      }),
      payloadResponseOnly: true,
    });
    const step = new tasks.LambdaInvoke(this, 'StartOrPollRestore', {
      lambdaFunction: executor,
      payloadResponseOnly: true,
      resultPath: '$.poll',
      retryOnServiceExceptions: false,
    });
    step.addRetry({
      errors: ['Lambda.ServiceException', 'Lambda.TooManyRequestsException'],
      maxAttempts: 2,
    });
    step.addCatch(failed, { resultPath: '$.error' });
    const wait = new sfn.Wait(this, 'WaitForBackup', {
      time: sfn.WaitTime.duration(Duration.seconds(30)),
    });
    const done = new sfn.Succeed(this, 'RestoreTrackingFinished');
    step.next(
      new sfn.Choice(this, 'RestoreTerminal')
        .when(sfn.Condition.booleanEquals('$.poll.terminal', true), done)
        .otherwise(wait.next(step)),
    );
    const workflow = new sfn.StateMachine(this, 'RestoreWorkflow', {
      definitionBody: sfn.DefinitionBody.fromChainable(step),
      timeout: Duration.hours(2),
      tracingEnabled: true,
    });
    workflow.grantStartExecution(props.api);
    props.api.addEnvironment('RESTORE_WORKFLOW_ARN', workflow.stateMachineArn);
    new events.Rule(this, 'RestoreWorkflowFailure', {
      eventPattern: {
        source: ['aws.states'],
        detailType: ['Step Functions Execution Status Change'],
        detail: {
          stateMachineArn: [workflow.stateMachineArn],
          status: ['FAILED', 'TIMED_OUT', 'ABORTED'],
        },
      },
      targets: [
        new targets.LambdaFunction(executor, { deadLetterQueue: deadLetters, retryAttempts: 2 }),
      ],
    });
    new cognito.CfnUserPoolGroup(this, 'RecoveryApprovers', {
      userPoolId: props.userPool.userPoolId,
      groupName: 'RecoveryApprovers',
      description: 'Independent approval of explicitly configured, billable AWS Backup restores',
    });
    Tags.of(this).add('Component', 'live-recovery');
    new CfnOutput(stack, 'LiveDiscoveryFunction', { value: discovery.functionName });
    new CfnOutput(stack, 'RestoreWorkflowArn', { value: workflow.stateMachineArn });
    new CfnOutput(stack, 'OperationalAlertsTopicArn', { value: alerts.topicArn });
  }
}
