## Getting Started: OCI Functions QuickStart

Follow these steps to set up your environment and deploy functions using Oracle Cloud Infrastructure (OCI) Functions:

### 1. Set Up Your Tenancy
- **Create Groups and Users:**
   - In the OCI Console, go to Identity & Security > Domains. Create groups and users as needed, and assign users to groups.
- **Create a Compartment:**
   - Go to Identity & Security > Compartments. Create a new compartment for your resources.
- **Create a VCN and Subnets:**
   - Go to Networking > Virtual Cloud Networks. Use the VCN Wizard to create a VCN with internet connectivity.
- **Create Policies:**
   - Go to Identity & Security > Policies. Use the Policy Builder to grant your group permissions to manage functions, networking, and related resources. Restrict policies to the minimum required compartments.

### 2. Create an Application
- Go to Developer Services > Functions > Applications. Create a new application, specifying the VCN and subnet.

### 3. Set Up Your Local Development Environment
- **Install Docker:**
   - Ensure Docker is installed and running (`docker version`, `docker run hello-world`).
- **Set Up API Signing Key and OCI Profile:**
   - In User Settings, generate and download an API key. Add it to `~/.oci/config` and set permissions (`chmod go-rwx ~/.oci/<private-key-file-name>.pem`).
- **Install Fn Project CLI:**
   - On macOS: `brew install fn` or use the install script from the Fn Project.
   - Confirm install with `fn version`.
- **Set Up Fn CLI Context:**
   - `fn create context <my-context> --provider oracle`
   - `fn use context <my-context>`
   - `fn update context oracle.profile <profile-name>`
   - `fn update context oracle.compartment-id <compartment-ocid>`
   - `fn update context api-url <api-endpoint>`
   - `fn update context registry <region-key>.ocir.io/<tenancy-namespace>/<repo-name-prefix>`
   - `fn update context oracle.image-compartment-id <compartment-ocid>`
- **Generate Auth Token:**
   - In User Settings, generate an Auth Token and save it securely.
- **Log in to OCI Registry:**
   - `docker login -u '<tenancy-namespace>/<user-name>' <region-key>.ocir.io` (use your Auth Token as the password)

### 4. Create, Deploy, and Invoke Your Function
- **Create Function:**
   - `fn init --runtime python <function-name>`
- **Deploy Function:**
   - `fn -v deploy --app <application-name>`
- **Invoke Function:**
   - `fn invoke <application-name> <function-name>`

For more details, see the [OCI Functions QuickStart Guide](https://docs.oracle.com/en-us/iaas/Content/Functions/Tasks/functionsquickstartlocalhost.htm).
# oci-object-copy-upon-write Copilot Instructions

## Project Overview
- Python 3.9+ OCI Function, deployed using Oracle Functions (FDK for Python)
- Uses OCI SDK as a dependency
- The function is triggered by an OCI Event from an Object Storage bucket
- The event contains a log of objects written to a bucket

## Function Requirements
1. **Backup Bucket Check/Create**
   - For each source bucket (e.g., `customerA-files`), check for a backup bucket named `<source-bucket>-backup` in a different compartment (compartment OCID is provided via function configuration).
   - If the backup bucket does not exist, create it as an archive bucket.
2. **Object Copy**
   - For each object in the event, copy the newly created object to the backup bucket at the same path.
   - Only new object creations are propagated (no deletes or updates).

## Implementation Guidelines
- Use Python 3.9+ syntax and typing
- Use Oracle Functions FDK for handler signature and deployment
- Use OCI Python SDK for all OCI API calls
- Read compartment OCID and other config from function configuration
- Ensure idempotency for bucket creation
- Do not propagate deletes or updates
- Provide clear logging for all actions

## Project Structure
- Main function handler in `func.py`
- Dependency management in `requirements.txt` (include `oci`, `fdk`)
- Example event payload and config in `README.md`
- Add tests and example usage as needed

## Development Best Practices
- Keep code modular and well-documented
- Use environment variables/config for all sensitive or environment-specific values
- Follow Oracle Functions deployment best practices

## Task Checklist
- [ ] Scaffold Python 3.9+ OCI Function project
- [ ] Add FDK and OCI SDK to requirements
- [ ] Implement handler logic for event-driven backup
- [ ] Add README.md with usage and deployment instructions
- [ ] Add example event payload and config
- [ ] Add tests if possible

## OCI IAM Policy Requirements

The function requires the following minimum permissions to operate on Object Storage:

```
Allow dynamic-group <function-dynamic-group> to manage buckets in compartment <backup-compartment-ocid> where all {request.permission = 'BUCKET_CREATE', request.permission = 'BUCKET_READ'}
Allow dynamic-group <function-dynamic-group> to manage objects in compartment <source-compartment-ocid> where all {request.permission = 'OBJECT_CREATE', request.permission = 'OBJECT_READ', request.permission = 'OBJECT_INSPECT'}
Allow dynamic-group <function-dynamic-group> to manage objects in compartment <backup-compartment-ocid> where all {request.permission = 'OBJECT_CREATE', request.permission = 'OBJECT_READ', request.permission = 'OBJECT_INSPECT'}
```

Replace `<function-dynamic-group>`, `<source-compartment-ocid>`, and `<backup-compartment-ocid>` with your actual values. These policies allow only the necessary actions for bucket creation/check and object copy, and do not allow deletes or updates.

## OCI Setup Instructions

### 1. Enable OCI Events for Object Write
- In the OCI Console, navigate to the Object Storage bucket you want to monitor.
- Go to the "Events" tab and create a new rule:
   - **Event Type:** Object - Create
   - **Target:** Oracle Functions (select your deployed function)
   - **Condition:** (Optional) Filter by object name or prefix as needed
- Save the rule. New object writes to the bucket will now trigger the function.

### 2. Create a Compartment for Backups
- In the OCI Console, go to "Identity & Security" > "Compartments".
- Click "Create Compartment" and provide a name (e.g., `backups` or `customer-backups`).
- Note the OCID of the new compartment; you will need it for the function configuration.

### 3. Function Configuration: Backup Compartment OCID
- When deploying the function, set a configuration variable (e.g., `BACKUP_COMPARTMENT_OCID`) with the OCID of the backup compartment.
- The function will use this OCID to create or check backup buckets.

### 4. Backup Bucket Creation
- The function will automatically create a backup bucket named `<source-bucket>-backup` in the backup compartment if it does not exist.
- The backup bucket will be created as an archive bucket for cost efficiency.
