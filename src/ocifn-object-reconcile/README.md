# Reconciliation function

`ocifn-object-reconcile` is a scheduled safety net for the event copy function. It compares a source bucket and its backup bucket, then submits server-side copies only for source object names missing from the destination. Deploy it once, then create one or more Resource Scheduler schedules; each schedule supplies its source bucket and optional object-name prefix in its invocation payload.

It pages through both `ListObjects` results in lexical order and merge-compares object names. There are no per-object `HEAD` requests and no object data passes through the function. One million objects in each bucket requires approximately 2,000 list requests, plus a `CopyObject` request for each missing object.

Before creating a missing backup bucket, the function checks that the source bucket exists. Bucket names and `SOURCE_PREFIX` object names are case-sensitive, so a mis-cased `SOURCE_BUCKET` fails without creating an empty backup bucket.

## Deploy and configure

From this directory:

```sh
fn -v deploy --app <application-name>
```

`BACKUP_COMPARTMENT_OCID` can be configured at the function application level and inherited by this function. Set it on the function only if it is not already inherited:

```sh
fn config function <application-name> ocifn-object-reconcile BACKUP_COMPARTMENT_OCID <backup-compartment-ocid>
```

Optional function configuration:

| Key | Default | Description |
| --- | --- | --- |
| `SOURCE_BUCKET` | None | Fallback source bucket for direct invocation. A schedule should normally provide this in its payload instead. |
| `BACKUP_BUCKET_SUFFIX` | `-backup` | Appended unchanged to `SOURCE_BUCKET` to derive the destination name. |
| `SOURCE_PREFIX` | All objects | Fallback object-name prefix. A schedule can override it to partition a large initial backfill. |
| `DEBUG` | Disabled | Enables detailed logs when set to `1`, `true`, `yes`, `y`, or `on`. INFO is the default. |

The scheduled payload can override `SOURCE_BUCKET`, `BACKUP_COMPARTMENT_OCID`, `BACKUP_BUCKET_SUFFIX`, and `SOURCE_PREFIX`. The function retrieves the tenancy's Object Storage namespace through the OCI SDK, so no namespace configuration is required. For example:

```json
{
  "SOURCE_BUCKET": "Customer_A",
  "SOURCE_PREFIX": "2026/"
}
```

Create a similar schedule for every source bucket. `SOURCE_PREFIX` is an Object Storage object-name prefix, not a filesystem path; omit it to reconcile the entire source bucket.

## Invoke from the command line

Use the same JSON payload for a direct, real reconciliation run. Fn CLI reads the invocation body from standard input:

```sh
fn invoke --content-type application/json <application-name> ocifn-object-reconcile < reconcile-payload.json
```

The JSON response is printed to the terminal for a direct invocation. Use a small prefix first because this command creates a missing backup bucket and submits copies for missing source objects.

## Schedule it weekly

1. In the OCI Console, open **Developer Services > Functions > Applications**, choose the application and `ocifn-object-reconcile`, then open **Schedules**.
2. Select **Add Schedule**, create a new schedule, choose **Weekly**, and select the UTC day and time. Add a JSON invocation payload with the `SOURCE_BUCKET` for that schedule (and `SOURCE_PREFIX` if desired).
3. Create the schedule and copy its OCID from Resource Scheduler.
4. Create a dynamic group for that schedule:

   ```
   ALL {resource.type='resourceschedule', resource.id='<resource-schedule-ocid>'}
   ```

5. Grant that dynamic group permission to invoke Functions:

   ```
   Allow dynamic-group <resource-scheduler-dynamic-group> to manage functions-family in tenancy
   ```

OCI invokes scheduled functions in detached mode. Set a detached invocation timeout appropriate for the expected listing and copy workload; OCI allows up to 3,600 seconds. For a large initial backfill, schedule one or more temporary prefix-partitioned runs using `SOURCE_PREFIX` rather than relying on a single unrestricted run.

## IAM and behavior

Follow the shared [setup guide](../../README_SETUP.md). The function needs permission to list/read objects in every source compartment covered by a schedule, plus permission to create/read backup buckets and create backup objects in the backup compartment.

The function does not delete destination objects, overwrite objects that already exist in the destination, or check object contents. The copy request uses OCI's destination `if-none-match` condition to protect against a destination object appearing between the listing and copy request. It is appropriate for write-once sources. Use OCI Logging and Functions metrics to audit scheduled runs; Resource Scheduler does not display the function's JSON result.
