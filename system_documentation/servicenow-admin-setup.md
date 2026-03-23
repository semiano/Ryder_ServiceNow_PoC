# ServiceNow Admin Setup Guide

This document is written for the **Ryder ServiceNow administrator** responsible for configuring the ServiceNow instance to support the Closed P1 RCA Processor integration.

It covers:
1. Integration user creation and role assignment
2. API key generation and handoff
3. Required ACL / table permissions
4. Flow Designer trigger for automatic closed-ticket push
5. Storing the Azure Function key securely
6. Validation steps

---

## 1. Integration User Account

### 1.1 Create a dedicated integration user

Navigate to **User Administration → Users** and create a new user:

| Field | Recommended value |
|---|---|
| User ID | `svc_rca_integration` (or similar naming convention) |
| First / Last name | `RCA Integration Service` |
| Email | service account email |
| Password | Strong, stored in a secrets vault |
| Active | ✅ Yes |
| Web service access only | ✅ Yes — prevents direct console login |
| Time zone | UTC |

### 1.2 Assign roles

The integration user needs the following roles. Assign via **User → Roles** tab:

| Role | Purpose |
|---|---|
| `itil` | Standard ITIL role — grants read access to `incident` table and most ITSM fields |
| `rest_service` | Required for REST API access |
| `create_task` | Required to POST new child incident records |

> **Minimum viable set:** `itil` + `rest_service` covers read. `create_task` is needed for child ticket creation. If your instance uses more restrictive ACLs, see Section 3 for field-level grants.

---

## 2. API Key Generation

### 2.1 Enable API key authentication (if not already enabled)

Navigate to **System OAuth → Application Registry** and confirm that **REST API integration** is enabled for the instance. This is enabled by default in most ServiceNow versions.

### 2.2 Generate an API key for the integration user

1. Navigate to **System Web Services → Scripted REST APIs** → or go directly to:
   `https://<your-instance>.service-now.com/nav_to.do?uri=sys_user.do?sysparm_query=user_name=svc_rca_integration`
2. Open the integration user record.
3. Select **Generate API Key** from the related links or user context menu.
4. Copy the generated token — it will be in the format `now_...`.
5. **Store this token securely** and do not display it again after initial generation.

### 2.3 Handoff

Provide the following values to the Microsoft integration team **via secure channel** (not email):

- `SERVICENOW_INSTANCE_URL` — e.g., `https://your-instance.service-now.com`
- `SERVICENOW_API_TOKEN` — the `now_...` token generated above
- Confirm auth scheme: `x-sn-apikey` (this is the header name used, not `Bearer`)

The integration team will store this token in **Azure Key Vault** under the secret name `SERVICENOW-API-TOKEN`. It will never be stored in application code or config files in the repository.

---

## 3. ACL / Table Permissions Required

The integration makes the following REST API calls. Each requires specific table and field-level ACL access for the integration user.

### 3.1 READ — Incident record by number or sys_id

**Endpoint:**
```
GET /api/now/table/incident?sysparm_query=number={number}&sysparm_fields=...
GET /api/now/table/incident/{sys_id}?sysparm_fields=...
```

**Fields that must be readable:**
```
sys_id, number, short_description, description, close_notes, state,
priority, severity, assignment_group, opened_at, closed_at, caller_id, cmdb_ci
```

**ACL check:** The `itil` role typically covers all of these. If `close_notes` is restricted by a custom ACL, it must be explicitly granted to the integration user or its role.

### 3.2 READ — Journal / work notes and comments

**Endpoint:**
```
GET /api/now/table/sys_journal_field
  ?sysparm_query=element_id={sys_id}^elementINwork_notes,comments
  &sysparm_fields=element,value,sys_created_on,sys_created_by
```

**ACL requirement:** Read access to `sys_journal_field` table for `work_notes` and `comments` elements. The `itil` role grants this, but if work_notes visibility is restricted to assignee groups, confirm the integration user can read all work_notes on P1 incidents.

### 3.3 READ — Similar incidents (optional enrichment)

**Endpoint:**
```
GET /api/now/table/incident
  ?sysparm_query=cmdb_ci={ci}^ORassignment_group={group}
  &sysparm_limit=6
  &sysparm_orderbyDESCoopened_at
```

Same field set as 3.1. No additional ACL required beyond `itil`.

### 3.4 CREATE — Child incident (RCA report record)

**Endpoint:**
```
POST /api/now/table/incident
```

**Fields written in the POST body:**
```
short_description, description, parent_incident, assignment_group, caller_id, cmdb_ci
```

**ACL requirement:**
- `create` operation on the `incident` table for the integration user
- The `create_task` role covers this in most standard ServiceNow configurations
- If your instance uses approval-required incident creation, the integration user may need to be exempted via ACL condition or assigned a group that bypasses approval on creation

> **Note:** The child ticket is created as a first-pass RCA report artifact. The `short_description` will be in the form `RCA Report: <parent ticket short description>`. The `description` field will contain the full structured RCA JSON and summary.

---

## 4. Flow Designer Trigger — Closed P1 Ticket Push

This is the automation that fires when a P1 incident is closed and pushes the ticket ID and status to the Azure Function endpoint, initiating RCA generation.

### 4.1 Create the Flow

Navigate to **Flow Designer** (search "Flow Designer" in the application navigator).

Click **New → Flow** and set:

| Field | Value |
|---|---|
| Flow name | `RCA Processor - Closed P1 Trigger` |
| Description | `Triggers the Azure RCA Processor Function when a P1 incident is closed` |
| Run As | `System User` (or the integration user) |
| Active | ✅ Yes (set after testing) |

### 4.2 Set the Trigger

Click **Add a trigger** → select **Record → Updated**:

| Setting | Value |
|---|---|
| Table | `Incident [incident]` |
| Condition | `State` **changes to** `Closed` **AND** `Priority` **is** `1 - Critical` |
| Run trigger | For each unique change (not batched) |

> This ensures the flow fires exactly once when the state transitions to Closed on a P1 ticket.

### 4.3 Add the HTTP Action

Click **Add an Action** → search for **REST** → select **REST Step**:

**Connection:**

| Field | Value |
|---|---|
| Connection type | Custom (no authentication — auth is handled via the function key in the URL) |
| Base URL | Leave blank — full URL is set in the request step |

**Request configuration:**

| Field | Value |
|---|---|
| Method | `POST` |
| URL | `https://<function-app-name>.azurewebsites.net/api/process-closed-ticket?code=<function-key>` |
| Headers | `Content-Type: application/json` |
| Request body | See below |

**Request body (JSON):**

In the body field, use the data pill picker to insert dynamic values from the trigger record:

```json
{
  "ticketId": "<pill: Incident Number>",
  "status": "closed"
}
```

In Flow Designer script notation:
```javascript
{
  "ticketId": fd_data.trigger.current.number,
  "status": "closed"
}
```

> The function accepts either the incident `number` (e.g., `INC0010002`) or the `sys_id`. Using `number` is recommended as it is more human-readable and appears in all log output.

### 4.4 Add error handling (recommended)

After the REST step, add a condition:

- **If** HTTP response status code **is not** `200` → log to a custom table or send a notification to the RCA operations group
- This ensures failed invocations are visible without requiring access to Azure Application Insights

### 4.5 Activate the flow

After testing (see Section 6), set the flow to **Active** and publish.

---

## 5. Storing the Azure Function Key in ServiceNow (Recommended)

Rather than hardcoding the Function key directly in the Flow URL, use the ServiceNow **Connection & Credential Aliases** store.

### 5.1 Create a Connection Alias

Navigate to **Connections & Credentials → Credentials**:

1. Click **New**
2. Type: **HTTP Header Authentication**
3. Name: `Azure RCA Processor Function Key`
4. Header name: leave blank (the key goes in the URL `?code=` param, not a header)

> **Alternative:** Use a Basic Auth credential where the password field holds `<function-key>` and reference it in the REST step. This keeps the key out of the flow definition UI.

### 5.2 Rotate the key

If the Azure Function key needs to be rotated (security event or scheduled rotation):
1. Generate a new key in the Azure Portal under **Function App → Functions → process-closed-ticket → Function Keys**
2. Update the credential in ServiceNow Connections & Credentials
3. Test with the validation steps in Section 6 before deactivating the old key

---

## 6. Validation Steps

### 6.1 Validate API key read access

From any REST client or the ServiceNow REST API Explorer:

```
GET https://<instance>.service-now.com/api/now/table/incident?sysparm_query=priority=1^state=Closed&sysparm_limit=1&sysparm_fields=sys_id,number,state,priority&sysparm_display_value=true
Headers:
  x-sn-apikey: <your-api-token>
  Accept: application/json
```

Expected: HTTP `200` with a `result` array containing a closed P1 incident.

### 6.2 Validate child record creation

```
POST https://<instance>.service-now.com/api/now/table/incident
Headers:
  x-sn-apikey: <your-api-token>
  Content-Type: application/json
  Accept: application/json
Body:
  {
    "short_description": "RCA Integration Test - safe to delete",
    "description": "Created by integration validation. Safe to delete.",
    "priority": "1",
    "state": "1"
  }
```

Expected: HTTP `201` with a `result` object containing a new `sys_id` and `number`. Delete this record after confirming.

### 6.3 Validate the Flow Designer trigger end-to-end

1. Identify a **non-production** P1 test incident (or create one in a dev/sandbox instance).
2. Close the ticket (set state = Closed).
3. Check the Flow Designer execution log: **Flow Designer → Executions** — confirm the flow triggered and the REST step returned HTTP `200`.
4. Check Azure Application Insights for the corresponding log entry with `ticketId` matching the test ticket number.

### 6.4 Confirm journal note access

```
GET https://<instance>.service-now.com/api/now/table/sys_journal_field?sysparm_query=element_id=<sys_id>^elementINwork_notes,comments&sysparm_fields=element,value&sysparm_limit=5
Headers:
  x-sn-apikey: <your-api-token>
```

Expected: HTTP `200` with a `result` array of journal entries. If this returns `403`, the integration user needs explicit read access to `sys_journal_field` for the relevant elements.

---

## 7. Summary Checklist for ServiceNow Admin

- [ ] Integration user `svc_rca_integration` created with `itil`, `rest_service`, `create_task` roles
- [ ] API key generated and provided to integration team via secure channel
- [ ] Confirmed read access to all required `incident` fields including `close_notes` and `work_notes`
- [ ] Confirmed POST create access to `incident` table
- [ ] Function key stored in Connections & Credentials (not hardcoded in flow)
- [ ] Flow Designer trigger created: condition = `state→Closed AND priority=1`, action = HTTP POST to Function endpoint
- [ ] End-to-end test completed in sandbox/dev instance
- [ ] Flow set to Active and published to production after successful test
