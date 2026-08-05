# Setup guide

> **Important:** This repository is sample code and is not supported by Oracle. Review all accompanying documentation, test thoroughly in a non-production environment, and use it carefully before relying on it for production, recovery, or compliance workloads.

This guide contains prerequisites shared by both functions. Function-specific configuration and deployment steps live in each function's README.

## OCI Functions prerequisites

1. Create an OCI Functions application in a subnet that can reach Object Storage through a Service Gateway.
2. Install Docker and the Fn Project CLI, then configure an Fn CLI context for the target region, compartment, and OCIR registry.
3. Create a protected backup compartment. The functions create backup buckets there, while application users should not have permission to delete backup objects or buckets.
4. Enable function logging in OCI Logging.

See the [OCI Functions quickstarts](https://docs.oracle.com/en-us/iaas/Content/Functions/Tasks/functionsquickstartguidestop.htm) for the full application and Fn CLI setup.

## Function dynamic group and policies

Create a dynamic group that includes the deployed functions. For a single function, use a rule similar to:

```
ALL {resource.type = 'fnfunc', resource.id = '<function-ocid>'}
```

Grant the function dynamic group the least privilege needed to inspect source objects, create/read backup buckets, and create/read backup objects. Adapt the compartment names and OCIDs to your tenancy:

```
Allow dynamic-group <function-dynamic-group> to {OBJECT_READ, BUCKET_READ} in compartment <source-compartment>
Allow dynamic-group <function-dynamic-group> to {OBJECT_READ, BUCKET_READ} in compartment <backup-compartment>
Allow dynamic-group <function-dynamic-group> to manage buckets in compartment <backup-compartment> where request.permission != 'BUCKET_DELETE'
Allow dynamic-group <function-dynamic-group> to manage objects in compartment <backup-compartment> where request.permission != 'OBJECT_DELETE'
```

The reconciliation function lists source and backup objects, so the `OBJECT_READ` and `BUCKET_READ` permissions are required in every source compartment covered by a schedule, as well as the backup compartment. Add one appropriately scoped source-compartment policy statement for each source compartment. The event copy function receives its namespace in the event; the reconciliation function retrieves the tenancy namespace through the OCI SDK.

## Object Storage event rule

Create one event rule per source bucket (or use an appropriately restrictive bucket-name condition):

1. In the OCI Console, open **Object Storage** and select the source bucket.
2. On **Events**, create a rule for **Object - Create**.
3. Choose the deployed event copy function as the target.
4. Save the rule and upload a test object.

The event rule must exclude backup buckets; otherwise a backup-object create event could invoke the copy function again.

## Retention and lifecycle

The functions create backup buckets with `Archive` as the bucket's default storage tier. Add an Object Storage retention rule if the backup must be protected from deletion for a defined period. Ensure the retention period and any lifecycle policy meet your organization's recovery and compliance requirements.
