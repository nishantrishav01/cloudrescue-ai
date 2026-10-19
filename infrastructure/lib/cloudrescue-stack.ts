import * as path from 'node:path';
import * as fs from 'node:fs';
import { Construct } from 'constructs';
import { DeciderFargate } from './decider-fargate';
import { LiveRecovery } from './live-recovery';
import {
  CfnOutput,
  Duration,
  RemovalPolicy,
  Size,
  Stack,
  StackProps,
  Tags,
  aws_apigatewayv2 as gateway,
  aws_apigatewayv2_authorizers as authorizers,
  aws_apigatewayv2_integrations as integrations,
  aws_cloudfront as cloudfront,
  aws_cloudfront_origins as origins,
  aws_cognito as cognito,
  aws_dynamodb as dynamodb,
  aws_ecr as ecr,
  aws_iam as iam,
  aws_lambda as lambda,
  aws_logs as logs,
  aws_s3 as s3,
  aws_s3_deployment as deployment,
  aws_stepfunctions as sfn,
  aws_stepfunctions_tasks as tasks,
} from 'aws-cdk-lib';

export class IncidentDataStack extends Stack {
  readonly incidentTable: dynamodb.Table;
  constructor(scope: Construct, id: string, props: StackProps) {
    super(scope, id, props);
    this.incidentTable = new dynamodb.Table(this, 'Incidents', {
      partitionKey: { name: 'id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      timeToLiveAttribute: 'ttl',
      removalPolicy: RemovalPolicy.RETAIN,
      deletionProtection: true,
    });
    this.incidentTable.addGlobalSecondaryIndex({
      indexName: 'owner-created',
      partitionKey: { name: 'owner', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'created_at', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });
    Tags.of(this.incidentTable).add('Component', 'storage');
    new CfnOutput(this, 'IncidentTableName', { value: this.incidentTable.tableName });
  }
}

export interface CloudRescueStackProps extends StackProps {
  readonly environment: string;
  readonly incidentTable: dynamodb.ITable;
  readonly assetPath?: string;
  readonly frontendPath?: string;
}

export class CloudRescueStack extends Stack {
  constructor(scope: Construct, id: string, props: CloudRescueStackProps) {
    super(scope, id, props);
    const root = path.resolve(__dirname, '../..');
    const packagePath = props.assetPath ?? path.join(root, 'backend/lambda.zip');
    const frontendPath = props.frontendPath ?? path.join(root, 'frontend/dist');
    if (!fs.existsSync(packagePath) || !fs.existsSync(frontendPath))
      throw new Error('Build frontend and run scripts/package-backend.ps1 before CDK synthesis.');
    const code = lambda.Code.fromAsset(packagePath, { exclude: ['**/__pycache__/**', '**/*.pyc'] });
    const table = props.incidentTable;
    const modelId = String(this.node.tryGetContext('bedrockModelId') ?? 'amazon.nova-lite-v1:0');
    if (!/^[a-z0-9.-]+:[a-z0-9]+$/.test(modelId))
      throw new Error('Invalid Bedrock model or profile ID.');
    const crossRegion = /^(apac|us|eu|global)\./.test(modelId);
    const modelRegions = (this.node.tryGetContext('bedrockModelRegions') ?? [
      this.region,
    ]) as string[];
    if (crossRegion && !this.node.tryGetContext('bedrockModelRegions'))
      throw new Error(
        'Cross-region inference requires an explicit list of verified destination regions.',
      );
    const baseModelId = crossRegion ? modelId.replace(/^[a-z]+\./, '') : modelId;
    const modelResources = modelRegions.map(
      (region) => `arn:${this.partition}:bedrock:${region}::foundation-model/${baseModelId}`,
    );
    if (crossRegion)
      modelResources.push(
        `arn:${this.partition}:bedrock:${this.region}:${this.account}:inference-profile/${modelId}`,
      );
    const baseEnvironment = {
      INCIDENT_TABLE: table.tableName,
      BEDROCK_MODEL_ID: modelId,
      POWERTOOLS_SERVICE_NAME: 'cloudrescue-ai',
    };

    const makeFunction = (name: string, handler: string, component: string, timeout: number) => {
      const logGroup = new logs.LogGroup(this, `${name}Logs`, {
        retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: RemovalPolicy.DESTROY,
      });
      const fn = new lambda.Function(this, name, {
        runtime: lambda.Runtime.PYTHON_3_12,
        architecture: lambda.Architecture.X86_64,
        code,
        handler,
        memorySize: 512,
        timeout: Duration.seconds(timeout),
        environment: baseEnvironment,
        logGroup,
        tracing: lambda.Tracing.ACTIVE,
      });
      Tags.of(fn).add('Component', component);
      Tags.of(logGroup).add('Component', 'observability');
      return fn;
    };
    const api = makeFunction('Api', 'backend.api.main.handler', 'api', 25);
    const agent = makeFunction('Agent', 'backend.agent.handler.handler', 'agent', 900);
    const simulator = makeFunction(
      'Simulator',
      'backend.simulation.handler.handler',
      'simulation',
      120,
    );
    const failure = makeFunction(
      'WorkflowFailure',
      'backend.common.failure.handler',
      'workflow',
      15,
    );
    for (const fn of [api, agent, simulator, failure]) table.grantReadWriteData(fn);
    for (const fn of [agent, simulator])
      fn.addToRolePolicy(
        new iam.PolicyStatement({ actions: ['bedrock:InvokeModel'], resources: modelResources }),
      );

    const imageUri = this.node.tryGetContext('deciderImageUri') as string | undefined;
    const enableSetting = this.node.tryGetContext('enableDecider');
    const inferenceEnabled =
      Boolean(imageUri) && enableSetting !== false && enableSetting !== 'false';
    if (imageUri && inferenceEnabled) {
      const image = imageUri.match(
        /^([0-9]+)\.dkr\.ecr\.([a-z0-9-]+)\.amazonaws\.com\/([a-z0-9/_-]+)@(sha256:[a-f0-9]{64})$/,
      );
      if (!image)
        throw new Error(
          'deciderImageUri must be a private ECR image URI pinned to a sha256 digest.',
        );
      const repository = ecr.Repository.fromRepositoryAttributes(this, 'DeciderRepository', {
        repositoryArn: `arn:${this.partition}:ecr:${image[2]}:${image[1]}:repository/${image[3]}`,
        repositoryName: image[3],
      });
      const runtime = String(this.node.tryGetContext('deciderRuntime') ?? 'lambda');
      if (runtime === 'fargate') {
        const inference = new DeciderFargate(this, 'DeciderFargate', {
          repository,
          digest: image[4],
        });
        inference.grantInvoke(agent);
        for (const [key, value] of Object.entries(inference.config))
          agent.addEnvironment(key, value);
        api.addEnvironment('BENCHMARK_BUCKET', inference.bucket.bucketName);
        inference.bucket.grantRead(api, 'benchmarks/*');
        new CfnOutput(this, 'DeciderRuntimeConfig', { value: JSON.stringify(inference.config) });
      } else if (runtime === 'lambda') {
        const inferenceLogs = new logs.LogGroup(this, 'DeciderLogs', {
          retention: logs.RetentionDays.ONE_MONTH,
          removalPolicy: RemovalPolicy.DESTROY,
        });
        const decider = new lambda.DockerImageFunction(this, 'Decider', {
          code: lambda.DockerImageCode.fromEcr(repository, { tagOrDigest: image[4] }),
          architecture: lambda.Architecture.X86_64,
          memorySize: 10240,
          timeout: Duration.seconds(840),
          ephemeralStorageSize: Size.mebibytes(2048),
          logGroup: inferenceLogs,
          environment: {
            HF_HUB_OFFLINE: '1',
            TRANSFORMERS_OFFLINE: '1',
            HOME: '/tmp',
            OMP_NUM_THREADS: '6',
          },
        });
        decider.grantInvoke(agent);
        agent.addEnvironment('DECIDER_FUNCTION', decider.functionName);
        Tags.of(decider).add('Component', 'inference');
      } else throw new Error('deciderRuntime must be lambda or fargate.');
    }

    const failureTask = new tasks.LambdaInvoke(this, 'Persist failure', {
      lambdaFunction: failure,
      payloadResponseOnly: true,
    });
    const task = (name: string, fn: lambda.IFunction) => {
      const invoke = new tasks.LambdaInvoke(this, name, {
        lambdaFunction: fn,
        payloadResponseOnly: true,
        resultPath: sfn.JsonPath.DISCARD,
        retryOnServiceExceptions: false,
      });
      invoke.addRetry({
        errors: [
          'Lambda.ServiceException',
          'Lambda.AWSLambdaException',
          'Lambda.SdkClientException',
          'Lambda.TooManyRequestsException',
        ],
        interval: Duration.seconds(2),
        maxAttempts: 2,
        backoffRate: 2,
      });
      invoke.addCatch(failureTask, { resultPath: '$.error' });
      return invoke;
    };
    const workflowLogs = new logs.LogGroup(this, 'WorkflowLogs', {
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    const definition = new sfn.Choice(this, 'Select incident phase')
      .when(
        sfn.Condition.stringEquals('$.phase', 'recovery'),
        task('Run approved virtual playbook', simulator),
      )
      .otherwise(task('Analyze evidence and validate decision', agent));
    const workflow = new sfn.StateMachine(this, 'IncidentWorkflow', {
      definitionBody: sfn.DefinitionBody.fromChainable(definition),
      stateMachineType: sfn.StateMachineType.STANDARD,
      timeout: Duration.minutes(20),
      logs: { destination: workflowLogs, level: sfn.LogLevel.ERROR, includeExecutionData: false },
      tracingEnabled: true,
    });
    workflow.grantStartExecution(api);
    api.addEnvironment('WORKFLOW_ARN', workflow.stateMachineArn);
    Tags.of(workflow).add('Component', 'workflow');

    const website = new s3.Bucket(this, 'Website', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.RETAIN,
      versioned: true,
    });
    const headers = new cloudfront.ResponseHeadersPolicy(this, 'SecurityHeaders', {
      securityHeadersBehavior: {
        contentTypeOptions: { override: true },
        frameOptions: { frameOption: cloudfront.HeadersFrameOption.DENY, override: true },
        referrerPolicy: {
          referrerPolicy: cloudfront.HeadersReferrerPolicy.NO_REFERRER,
          override: true,
        },
        strictTransportSecurity: {
          accessControlMaxAge: Duration.days(365),
          includeSubdomains: true,
          override: true,
        },
        contentSecurityPolicy: {
          contentSecurityPolicy:
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self' https://*.amazonaws.com https://*.amazoncognito.com; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
          override: true,
        },
      },
    });
    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      defaultRootObject: 'index.html',
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(website),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        responseHeadersPolicy: headers,
        compress: true,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      },
      additionalBehaviors: {
        'config.json': {
          origin: origins.S3BucketOrigin.withOriginAccessControl(website),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          responseHeadersPolicy: headers,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        },
        'index.html': {
          origin: origins.S3BucketOrigin.withOriginAccessControl(website),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          responseHeadersPolicy: headers,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        },
      },
      priceClass: cloudfront.PriceClass.PRICE_CLASS_200,
    });
    const siteUrl = `https://${distribution.distributionDomainName}`;
    Tags.of(website).add('Component', 'web');
    Tags.of(distribution).add('Component', 'web');

    const userPool = new cognito.UserPool(this, 'Operators', {
      selfSignUpEnabled: false,
      signInAliases: { email: true },
      passwordPolicy: {
        minLength: 14,
        requireDigits: true,
        requireLowercase: true,
        requireUppercase: true,
        requireSymbols: true,
      },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      removalPolicy: RemovalPolicy.RETAIN,
    });
    const client = userPool.addClient('WarRoomClient', {
      authFlows: { userSrp: true, adminUserPassword: true },
      generateSecret: false,
      preventUserExistenceErrors: true,
      oAuth: {
        flows: { authorizationCodeGrant: true },
        scopes: [cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL],
        callbackUrls: [siteUrl],
        logoutUrls: [siteUrl],
      },
      accessTokenValidity: Duration.hours(1),
      idTokenValidity: Duration.hours(1),
      enableTokenRevocation: true,
    });
    const domain = userPool.addDomain('OperatorDomain', {
      cognitoDomain: { domainPrefix: `cloudrescue-${this.account}-${props.environment}` },
    });
    Tags.of(userPool).add('Component', 'identity');
    new LiveRecovery(this, 'LiveRecovery', { code, api, agent, table, userPool });
    const authorizer = new authorizers.HttpJwtAuthorizer(
      'OperatorsOnly',
      userPool.userPoolProviderUrl,
      { jwtAudience: [client.userPoolClientId] },
    );
    const httpApi = new gateway.HttpApi(this, 'HttpApi', {
      defaultAuthorizer: authorizer,
      createDefaultStage: true,
      corsPreflight: {
        allowOrigins: [siteUrl],
        allowHeaders: ['authorization', 'content-type'],
        allowMethods: [gateway.CorsHttpMethod.GET, gateway.CorsHttpMethod.POST],
        maxAge: Duration.hours(1),
      },
    });
    const integration = new integrations.HttpLambdaIntegration('FastApiIntegration', api);
    httpApi.addRoutes({
      path: '/health',
      methods: [gateway.HttpMethod.GET],
      integration,
      authorizer: new gateway.HttpNoneAuthorizer(),
    });
    httpApi.addRoutes({
      path: '/api/{proxy+}',
      methods: [gateway.HttpMethod.GET, gateway.HttpMethod.POST],
      integration,
    });
    const stage = httpApi.defaultStage?.node.defaultChild as gateway.CfnStage;
    stage.defaultRouteSettings = {
      throttlingBurstLimit: 10,
      throttlingRateLimit: 5,
      detailedMetricsEnabled: true,
    };
    Tags.of(httpApi).add('Component', 'api');
    new deployment.BucketDeployment(this, 'DeployWebsite', {
      sources: [
        deployment.Source.asset(frontendPath),
        deployment.Source.jsonData('config.json', {
          mode: 'aws',
          apiUrl: httpApi.apiEndpoint,
          region: this.region,
          userPoolId: userPool.userPoolId,
          clientId: client.userPoolClientId,
          authDomain: domain.domainName + `.auth.${this.region}.amazoncognito.com`,
        }),
      ],
      destinationBucket: website,
      distribution,
      distributionPaths: ['/*'],
      cacheControl: [deployment.CacheControl.maxAge(Duration.minutes(5))],
      prune: false,
    });
    new CfnOutput(this, 'WebsiteUrl', { value: siteUrl });
    new CfnOutput(this, 'ApiUrl', { value: httpApi.apiEndpoint });
    new CfnOutput(this, 'UserPoolId', { value: userPool.userPoolId });
    new CfnOutput(this, 'UserPoolClientId', { value: client.userPoolClientId });
    new CfnOutput(this, 'WorkflowArn', { value: workflow.stateMachineArn });
    new CfnOutput(this, 'InferenceStatus', {
      value: inferenceEnabled
        ? `Decider enabled using ${String(this.node.tryGetContext('deciderRuntime') ?? 'lambda')}; model results measured separately`
        : 'Inference disabled: incidents fail closed and escalate',
    });
  }
}
