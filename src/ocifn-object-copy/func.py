import io
import json
import logging

from fdk import response


def handler(ctx, data: io.BytesIO = None):
    import oci
    logger = logging.getLogger()
    backup_compartment_ocid = ctx.Config().get("BACKUP_COMPARTMENT_OCID", "NOT_SET")
    logger.info(f"Configured BACKUP_COMPARTMENT_OCID: {backup_compartment_ocid}")

    try:
        event_json = json.loads(data.getvalue())
        logger.info(f"Incoming event JSON: {json.dumps(event_json)}")
    except Exception as ex:
        logger.error(f"Error parsing event JSON: {ex}")
        return response.Response(
            ctx,
            response_data=json.dumps({"error": "Invalid event JSON"}),
            headers={"Content-Type": "application/json"}
        )

    # Extract resourceId and bucketName
    try:
        resource_id = event_json["data"]["resourceId"]
        bucket_name = event_json["data"]["additionalDetails"]["bucketName"]
        logger.info(f"resourceId: {resource_id}, bucketName: {bucket_name}")
    except Exception as ex:
        logger.error(f"Missing resourceId or bucketName: {ex}")
        return response.Response(
            ctx,
            response_data=json.dumps({"error": "Missing resourceId or bucketName"}),
            headers={"Content-Type": "application/json"}
        )

    # Set up OCI clients
    signer = oci.auth.signers.get_resource_principals_signer()
    object_storage = oci.object_storage.ObjectStorageClient({}, signer=signer)

    # Get namespace
    try:
        namespace = event_json["data"]["additionalDetails"]["namespace"]
    except Exception as ex:
        logger.error(f"Missing namespace: {ex}")
        return response.Response(
            ctx,
            response_data=json.dumps({"error": "Missing namespace"}),
            headers={"Content-Type": "application/json"}
        )

    # Prepare backup bucket name
    backup_bucket_name = f"{bucket_name}-backup"

    # Check if backup bucket exists, create if not
    try:
        object_storage.get_bucket(namespace, backup_bucket_name)
        logger.info(f"Backup bucket {backup_bucket_name} exists.")
    except oci.exceptions.ServiceError as e:
        if e.status == 404:
            logger.info(f"Backup bucket {backup_bucket_name} not found. Creating as archive bucket.")
            object_storage.create_bucket(
                namespace,
                oci.object_storage.models.CreateBucketDetails(
                    name=backup_bucket_name,
                    compartment_id=backup_compartment_ocid,
                    storage_tier="Archive"
                )
            )
        else:
            logger.error(f"Error checking/creating backup bucket: {e}")
            return response.Response(
                ctx,
                response_data=json.dumps({"error": "Failed to get or create backup bucket"}),
                headers={"Content-Type": "application/json"}
            )

    # Download the object from the source bucket
    try:
        object_name = event_json["data"]["resourceName"]
        get_obj = object_storage.get_object(namespace, bucket_name, object_name)
        object_data = get_obj.data.content
        logger.info(f"Downloaded object {object_name} from {bucket_name}")
    except Exception as ex:
        logger.error(f"Failed to download object: {ex}")
        return response.Response(
            ctx,
            response_data=json.dumps({"error": "Failed to download object"}),
            headers={"Content-Type": "application/json"}
        )

    # Write the object to the backup bucket
    try:
        object_storage.put_object(
            namespace,
            backup_bucket_name,
            object_name,
            io.BytesIO(object_data)
        )
        logger.info(f"Copied object {object_name} to backup bucket {backup_bucket_name}")
    except Exception as ex:
        logger.error(f"Failed to copy object to backup bucket: {ex}")
        return response.Response(
            ctx,
            response_data=json.dumps({"error": "Failed to copy object to backup bucket"}),
            headers={"Content-Type": "application/json"}
        )

    return response.Response(
        ctx,
        response_data=json.dumps({
            "status": "success",
            "source_bucket": bucket_name,
            "backup_bucket": backup_bucket_name,
            "object": object_name
        }),
        headers={"Content-Type": "application/json"}
    )
