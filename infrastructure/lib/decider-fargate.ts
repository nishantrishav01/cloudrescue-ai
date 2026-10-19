import * as path from 'node:path';
import { Construct } from 'constructs';
import {
  Duration,
  RemovalPolicy,
  Tags,
  aws_ec2 as ec2,
  aws_ecs as ecs,
  aws_ecr as ecr,
  aws_iam as iam,
  aws_logs as logs,
  aws_s3 as s3,
  aws_s3_assets as assets,
} from 'aws-cdk-lib';

export interface DeciderFargateProps {
  readonly repository: ecr.IRepository;
  readonly digest: string;
}

export class DeciderFargate extends Construct {
  readonly bucket: s3.Bucket;
  readonly config: Record<string, string>;
  readonly taskDefinition: ecs.FargateTaskDefinition;
  readonly cluster: ecs.Cluster;

  constructor(scope: Construct, id: string, props: DeciderFargateProps) {
    super(scope, id);
    const existingNetwork = this.node.tryGetContext('inferenceNetwork') as
      | ec2.VpcAttributes
      | undefined;
    const vpc = existingNetwork
      ? ec2.Vpc.fromVpcAttributes(this, 'Vpc', existingNetwork)
      : new ec2.Vpc(this, 'Vpc', {
          maxAzs: 2,
          natGateways: 0,
          subnetConfiguration: [
            { name: 'Inference', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
          ],
        });
    const securityGroup = new ec2.SecurityGroup(this, 'TaskNetwork', {
      vpc,
      allowAllOutbound: false,
      description: 'No inbound access; outbound HTTPS for ECR, logs and private S3 jobs',
    });
    securityGroup.addEgressRule(ec2.Peer.anyIpv4(), ec2.Port.tcp(443));
    this.cluster = new ecs.Cluster(this, 'Cluster', { vpc });
    this.bucket = new s3.Bucket(this, 'Jobs', {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.RETAIN,
      lifecycleRules: [
        { prefix: 'requests/', expiration: Duration.days(2) },
        { prefix: 'results/', expiration: Duration.days(30) },
      ],
    });
    const source = new assets.Asset(this, 'WorkerSource', {
      path: path.resolve(__dirname, '../../backend/decider/worker.py'),
    });
    const logGroup = new logs.LogGroup(this, 'Logs', {
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    const principal = new iam.ServicePrincipal('ecs-tasks.amazonaws.com', {
      conditions: {
        StringEquals: { 'aws:SourceAccount': this.cluster.stack.account },
        ArnLike: {
          'aws:SourceArn': `arn:${this.cluster.stack.partition}:ecs:${this.cluster.stack.region}:${this.cluster.stack.account}:*`,
        },
      },
    });
    const taskRole = new iam.Role(this, 'TaskRole', { assumedBy: principal });
    const executionRole = new iam.Role(this, 'ExecutionRole', { assumedBy: principal });
    this.taskDefinition = new ecs.FargateTaskDefinition(this, 'Task', {
      cpu: 4096,
      memoryLimitMiB: 12288,
      taskRole,
      executionRole,
      runtimePlatform: {
        cpuArchitecture: ecs.CpuArchitecture.X86_64,
        operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
      },
    });
    source.grantRead(this.taskDefinition.taskRole);
    this.bucket.grantRead(this.taskDefinition.taskRole, 'requests/*');
    this.bucket.grantPut(this.taskDefinition.taskRole, 'results/*');
    this.taskDefinition.addContainer('DeciderWorker', {
      image: ecs.ContainerImage.fromEcrRepository(props.repository, props.digest),
      entryPoint: ['python', '-c'],
      command: [
        "import boto3,os; source=boto3.client('s3').get_object(Bucket=os.environ['WORKER_BUCKET'],Key=os.environ['WORKER_KEY'])['Body'].read(); exec(compile(source,'worker.py','exec'),{'__name__':'__main__'})",
      ],
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: 'decider',
        logGroup,
        mode: ecs.AwsLogDriverMode.BLOCKING,
      }),
      environment: {
        WORKER_BUCKET: source.s3BucketName,
        WORKER_KEY: source.s3ObjectKey,
        INFERENCE_BUCKET: this.bucket.bucketName,
        HF_HUB_OFFLINE: '1',
        TRANSFORMERS_OFFLINE: '1',
        HOME: '/tmp',
        OMP_NUM_THREADS: '4',
      },
      stopTimeout: Duration.seconds(30),
    });
    this.config = {
      DECIDER_RUNTIME: 'fargate',
      INFERENCE_BUCKET: this.bucket.bucketName,
      FARGATE_CLUSTER: this.cluster.clusterArn,
      FARGATE_TASK_DEFINITION: this.taskDefinition.taskDefinitionArn,
      FARGATE_SUBNETS: vpc.publicSubnets.map((subnet) => subnet.subnetId).join(','),
      FARGATE_SECURITY_GROUP: securityGroup.securityGroupId,
    };
    Tags.of(this).add('Component', 'inference');
  }

  grantInvoke(grantee: iam.IGrantable) {
    this.bucket.grantPut(grantee, 'requests/*');
    this.bucket.grantRead(grantee, 'results/*');
    this.taskDefinition.grantRun(grantee);
    iam.Grant.addToPrincipal({
      grantee,
      actions: ['ecs:TagResource'],
      resourceArns: [
        `arn:${this.taskDefinition.stack.partition}:ecs:${this.taskDefinition.stack.region}:${this.taskDefinition.stack.account}:task/${this.cluster.clusterName}/*`,
      ],
      conditions: { StringEquals: { 'ecs:CreateAction': 'RunTask' } },
    });
    iam.Grant.addToPrincipal({
      grantee,
      actions: ['ecs:DescribeTasks', 'ecs:StopTask'],
      resourceArns: [
        `arn:${this.taskDefinition.stack.partition}:ecs:${this.taskDefinition.stack.region}:${this.taskDefinition.stack.account}:task/${this.cluster.clusterName}/*`,
      ],
    });
  }
}
