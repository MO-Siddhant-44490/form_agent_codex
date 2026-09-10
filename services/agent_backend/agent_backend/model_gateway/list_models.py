"""List Bedrock Anthropic models available to the current credentials, so the
right BEDROCK_MODEL_ID can be chosen for this account/region.

    aws sso login --profile dev
    AWS_PROFILE=dev uv run python -m agent_backend.model_gateway.list_models
"""

import os


def main() -> None:
    import boto3

    region = os.environ.get("AWS_REGION", "ap-south-1")
    profile = os.environ.get("AWS_PROFILE")
    session = boto3.Session(profile_name=profile)
    client = session.client("bedrock", region_name=region)

    print(f"Bedrock foundation models in {region} (profile={profile or 'default'}):\n")
    models = client.list_foundation_models().get("modelSummaries", [])
    anthropic = [m for m in models if "anthropic" in m["modelId"].lower()]
    for m in anthropic:
        print(f"  {m['modelId']}  ({m.get('modelName', '?')})")
    if not anthropic:
        print("  (no Anthropic models listed — enable model access in the Bedrock console)")

    # Cross-region inference profiles (the IDs you usually call in APAC/US).
    try:
        profiles = client.list_inference_profiles().get("inferenceProfileSummaries", [])
        ap = [p for p in profiles if "anthropic" in p["inferenceProfileId"].lower()]
        if ap:
            print("\nInference profiles (use these IDs for BEDROCK_MODEL_ID):")
            for p in ap:
                print(f"  {p['inferenceProfileId']}  ({p.get('inferenceProfileName', '?')})")
    except Exception as error:
        print(f"\n(could not list inference profiles: {error})")


if __name__ == "__main__":
    main()
