import argparse
import json

import boto3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("email")
    parser.add_argument("--outputs", default="infrastructure/outputs.json")
    arguments = parser.parse_args()
    with open(arguments.outputs, encoding="utf-8") as outputs_file:
        outputs = json.load(outputs_file)
    stack = next(value for value in outputs.values() if "UserPoolId" in value)
    client = boto3.client("cognito-idp")
    try:
        response = client.admin_create_user(
            UserPoolId=stack["UserPoolId"],
            Username=arguments.email,
            UserAttributes=[
                {"Name": "email", "Value": arguments.email},
                {"Name": "email_verified", "Value": "true"},
            ],
            DesiredDeliveryMediums=["EMAIL"],
        )
    except client.exceptions.UsernameExistsException:
        print("Operator already exists; no duplicate invitation was sent.")
        return
    print(
        json.dumps(
            {
                "username": arguments.email,
                "status": response["User"]["UserStatus"],
                "invitation": "requested via Cognito email",
            }
        )
    )


if __name__ == "__main__":
    main()
