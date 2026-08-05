# Event copy function

`ocifn-object-copy` is invoked by an OCI Object Storage **Object - Create** event. It copies the created object to an Archive-tier backup bucket through OCI's server-side `CopyObject` API.

## Behavior

1. Reads the source namespace, bucket, and object name from the event payload.
2. Confirms that the source bucket still exists. If it does not, the function stops without creating a backup bucket.
3. Derives the backup bucket name from the source bucket plus `BACKUP_BUCKET_SUFFIX`.
4. Reuses an existing backup bucket or creates it in `BACKUP_COMPARTMENT_OCID` with Archive as its default tier.
5. Submits a server-side copy of the object at the same object name.

The function never downloads object contents. Its return body is visible to direct invokers only; for event-driven copies, use OCI Logging and Functions metrics.

## Deploy

From this directory:

```sh
fn -v deploy --app <application-name>
```

Configure the deployed function:

```sh
fn config function <application-name> ocifn-object-copy BACKUP_COMPARTMENT_OCID <backup-compartment-ocid>
fn config function <application-name> ocifn-object-copy BACKUP_BUCKET_SUFFIX -backup
```

## Configuration

| Key | Required | Default | Description |
| --- | --- | --- | --- |
| `BACKUP_COMPARTMENT_OCID` | Yes | None | Backup-compartment OCID used if the destination bucket must be created. |
| `BACKUP_BUCKET_SUFFIX` | No | `-backup` | Text appended unchanged to the source bucket name. |
| `DEBUG` | No | Disabled | Enables debug logs when `1`, `true`, `yes`, `y`, or `on`. INFO is the default log level. |

OCI supplies `OCI_RESOURCE_PRINCIPAL_REGION` to the deployed function. Do not configure it manually.

Bucket names and object names are case-sensitive. The function uses the bucket name exactly as supplied by the event; a missing source bucket prevents destination-bucket creation.

## Event rule and IAM

Follow the shared [setup guide](../../README_SETUP.md) to create the Object Create event rule and required dynamic-group policies.

## Local invocation

Use [sample-event.json](sample-event.json) as the event payload:

```sh
fn invoke --content-type application/json <application-name> ocifn-object-copy < sample-event.json
```
