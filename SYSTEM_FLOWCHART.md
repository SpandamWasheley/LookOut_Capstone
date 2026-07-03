# LookOut Barangay Security System — Complete System Flowchart

---

## 1. Authentication Flow (All Portals)

```mermaid
flowchart TD
    START([User Opens System]) --> PORTAL{Which Portal?}

    PORTAL -->|Web Browser| WEB_LOGIN[Web Login Page\nUsername + Password]
    PORTAL -->|Mobile App| MOB_LOGIN[Officer App Login\nUsername + Password]

    WEB_LOGIN --> WEB_AUTH{Authenticate}
    WEB_AUTH -->|Invalid| WEB_ERR[Show Error Message]
    WEB_ERR --> WEB_LOGIN
    WEB_AUTH -->|Forgot Password| FORGOT[Forgot Password Page]
    FORGOT --> FP_EMAIL[Enter Registered Email]
    FP_EMAIL --> FP_CODE[Enter OTP Code sent to Email]
    FP_CODE --> FP_RESET[Set New Password]
    FP_RESET --> WEB_LOGIN

    WEB_AUTH -->|Valid - Officer role on web| WEB_DENY[Denied — Use Mobile App]
    WEB_AUTH -->|Valid - Admin| MUST_CHG_A{Must Change\nPassword?}
    WEB_AUTH -->|Valid - Dispatcher| MUST_CHG_D{Must Change\nPassword?}
    WEB_AUTH -->|Valid - Both role| MUST_CHG_B{Must Change\nPassword?}

    MUST_CHG_A -->|Yes| CHG_PW_A[Force Change Password Page]
    CHG_PW_A --> ADMIN_DASH[Admin Dashboard]
    MUST_CHG_A -->|No| ADMIN_DASH

    MUST_CHG_D -->|Yes| CHG_PW_D[Force Change Password Page]
    CHG_PW_D --> DISP_DASH[Dispatcher Dashboard]
    MUST_CHG_D -->|No| DISP_DASH

    MUST_CHG_B -->|Yes| CHG_PW_B[Force Change Password Page]
    CHG_PW_B --> BOTH_DASH[Both-Role Dashboard]
    MUST_CHG_B -->|No| BOTH_DASH

    MOB_LOGIN --> MOB_AUTH{Authenticate\nOfficer Only}
    MOB_AUTH -->|Invalid| MOB_ERR[Show Error Message]
    MOB_ERR --> MOB_LOGIN
    MOB_AUTH -->|Forgot Password| MOB_FORGOT[Forgot Password Screen]
    MOB_FORGOT --> MOB_FP_CODE[Enter Email → OTP → New Password]
    MOB_FP_CODE --> MOB_LOGIN
    MOB_AUTH -->|Valid| MOB_MUST{Must Change\nPassword?}
    MOB_MUST -->|Yes| MOB_CHG[Change Password Screen]
    MOB_CHG --> OFFICER_APP[Officer App — Tabs]
    MOB_MUST -->|No| OFFICER_APP

    ADMIN_DASH --> LOGOUT_A([Logout → Web Login])
    DISP_DASH --> LOGOUT_D([Logout → Web Login])
    BOTH_DASH --> LOGOUT_B([Logout → Web Login])
    OFFICER_APP --> LOGOUT_M([Logout → Mobile Login])
```

---

## 2. Admin Web Dashboard — Full Flow

```mermaid
flowchart TD
    ADMIN_DASH([Admin Dashboard]) --> SIDEBAR{Sidebar Navigation}

    SIDEBAR --> PG_OVERVIEW[Overview / Dashboard]
    SIDEBAR --> PG_CAMERAS[Cameras]
    SIDEBAR --> PG_VIOLATIONS[Violations / Alerts]
    SIDEBAR --> PG_RECORDS[Records]
    SIDEBAR --> PG_RESLOG[Resident Log]
    SIDEBAR --> PG_RESIDENTS[Resident Database]
    SIDEBAR --> PG_OFFICERS[Officers & Personnel]
    SIDEBAR --> PG_CONFIG[System Settings]

    %% Overview Page
    PG_OVERVIEW --> OV_KPI[KPI Cards:\nActive Violations · Cameras Online\nOfficers on Duty · Total Alerts]
    PG_OVERVIEW --> OV_FEEDS[Live Camera Feeds Preview]
    PG_OVERVIEW --> OV_RECENT[Recent Violations Feed]
    OV_FEEDS --> PG_CAMERAS
    OV_RECENT --> PG_VIOLATIONS

    %% Cameras Page
    PG_CAMERAS --> CAM_GRID[Camera Grid\nAll registered cameras]
    CAM_GRID --> CAM_STATUS{Camera Status}
    CAM_STATUS --> CAM_ONLINE[Online — Live Feed]
    CAM_STATUS --> CAM_DEGRADE[Degraded — Partial Feed]
    CAM_STATUS --> CAM_OFFLINE[Offline — No Feed]

    %% Violations Page
    PG_VIOLATIONS --> VIO_FILTER{Filter Tab}
    VIO_FILTER --> VIO_ALL[All Active\nActive + Dispatched]
    VIO_FILTER --> VIO_ACTIVE[Active Only]
    VIO_FILTER --> VIO_DISP[Dispatched Only]

    VIO_ALL & VIO_ACTIVE & VIO_DISP --> VIO_CARD[Click Violation Card]
    VIO_CARD --> VIO_MODAL[Violation Detail Modal\nEvidence · Location · Time\nConfidence · Description\nAssigned Officers]

    VIO_MODAL --> VM_STATUS{Alert Status}

    VM_STATUS -->|Active| VM_ACT_ACTIONS[Active Actions]
    VM_ACT_ACTIONS --> VM_DISMISS[Dismiss Alert]
    VM_ACT_ACTIONS --> VM_DISPATCH[Dispatch Officers]
    VM_ACT_ACTIONS --> VM_CONTACT_ACT{Curfew Violation?}
    VM_CONTACT_ACT -->|Yes| VM_GUARDIAN[Contact Guardian\nSMS to household]

    VM_STATUS -->|Dispatched| VM_DSP_ACTIONS[Dispatched Actions]
    VM_DSP_ACTIONS --> VM_REASSIGN[Reassign Officers]
    VM_DSP_ACTIONS --> VM_RESOLVE[Mark as Resolved]
    VM_DSP_ACTIONS --> VM_CONTACT_DSP{Curfew Violation?}
    VM_CONTACT_DSP -->|Yes| VM_GUARDIAN

    VM_DISMISS --> DM_MODAL[Dismiss Modal\nSelect Reason\nOptional Notes]
    DM_MODAL --> DM_CONFIRM[Confirm Dismissal\nStatus → Acknowledged]

    VM_DISPATCH --> DISPATCH_MODAL[Dispatch Modal\nAvailable Officers List\nSelect Multiple Officers]
    DISPATCH_MODAL --> DSP_CONFIRM[Confirm Dispatch\nStatus → Dispatched\nOfficers Assigned]

    VM_RESOLVE --> RESOLVE_CHK[Resolve Checklist Modal\nAll Registered Residents Listed\nFace Recognition Match\nLabeled Possible Candidate]
    RESOLVE_CHK --> RES_SELECT[Check Involved Residents]
    RES_SELECT --> RES_CONFIRM[Confirm & Resolve\nStatus → Resolved\nViolation Linked to Resident Log]

    VM_GUARDIAN --> GDN_SEARCH[Search Households]
    GDN_SEARCH --> GDN_SELECT[Select Phone Numbers]
    GDN_SELECT --> GDN_MSG[Edit SMS Message]
    GDN_MSG --> GDN_SEND[Send SMS via Semaphore]

    %% Records Page
    PG_RECORDS --> REC_FILTER{Filter}
    REC_FILTER --> REC_ALL[All Closed]
    REC_FILTER --> REC_RESOLVED[Resolved Only]
    REC_FILTER --> REC_DISMISSED[Dismissed Only]
    REC_ALL & REC_RESOLVED & REC_DISMISSED --> REC_SEARCH[Search by Type / Name]
    REC_SEARCH --> REC_CARD[Click Record]
    REC_CARD --> REC_DETAIL[View Violation Detail Modal\nRead-only Evidence + Full Info]

    %% Resident Log Page
    PG_RESLOG --> RL_LIST[All Tracked Residents]
    RL_LIST --> RL_SEARCH[Search by Name / Barangay ID]
    RL_SEARCH --> RL_PERSON[Select Person]
    RL_PERSON --> RL_PROFILE[Resident Violation Profile\nTotal Violation Count\nViolation History List]
    RL_PROFILE --> RL_VIO[Click Violation Entry\nView Full Violation Details]

    %% Resident Database Page
    PG_RESIDENTS --> RES_VIEW{View Mode}
    RES_VIEW --> RES_HOUSEHOLDS[Household List View]
    RES_VIEW --> RES_TABLE[Resident Table View]

    RES_HOUSEHOLDS --> RES_ADD_HH[Add Household Button]
    RES_ADD_HH --> HH_MODAL[Add Household Modal\nFamily Name · Address · Purok\nZone · Contact · Enrolled Date]
    HH_MODAL --> HH_SAVE[Save Household]

    RES_HOUSEHOLDS --> HH_EXPAND[Expand Household Card]
    HH_EXPAND --> HH_MEMBER[View Members List]
    HH_MEMBER --> MEM_ADD[Add Member]
    MEM_ADD --> MEM_MODAL[Member Form\nFirst/Last Name · Birthdate\nRelation · Barangay ID\nPhone · Status]
    MEM_MODAL --> MEM_SAVE[Save Member]
    HH_MEMBER --> MEM_EDIT[Edit Member Info]
    HH_MEMBER --> MEM_ENROLL[Enroll Face]
    MEM_ENROLL --> ENROLL_MODAL[Enrollment Modal\nUpload / Capture Face Photo\nAdd to Face Database]
    ENROLL_MODAL --> ENROLL_SAVE[Face Saved to face_db.json\nUsed by AI Curfew Detection]

    %% Officers Page
    PG_OFFICERS --> OFF_TABS{Tab}
    OFF_TABS --> OFF_LIST[Officers List\nStatus · Badge · Location]
    OFF_TABS --> DISP_LIST[Dispatchers List\nUsername · Email · Role]

    OFF_LIST --> OFF_ADD[Register New Officer / Both]
    OFF_ADD --> OFF_EMAIL[Enter Officer Email]
    OFF_EMAIL --> OFF_CODE[Send Verification Code to Email]
    OFF_CODE --> OFF_VERIFY[Enter Code to Verify]
    OFF_VERIFY --> OFF_FORM[Fill Officer Details\nName · Badge · Location\nPhone · Username · Password]
    OFF_FORM --> OFF_SAVE[Officer Account Created\nCan Login via Mobile App]

    OFF_LIST --> OFF_EDIT[Edit Officer\nStatus · Location · Badge]
    OFF_LIST --> OFF_DELETE[Delete Officer]

    DISP_LIST --> DISP_ADD[Register Dispatcher]
    DISP_ADD --> DISP_FORM[Fill Dispatcher Details\nName · Email · Username · Password]
    DISP_FORM --> DISP_SAVE[Dispatcher Account Created\nCan Login via Web]
    DISP_LIST --> DISP_DELETE[Delete Dispatcher]

    %% System Config Page
    PG_CONFIG --> CFG_TABS{Settings Section}
    CFG_TABS --> CFG_CURFEW[Curfew Settings\nCurfew Hours · Age Limit\nAI Confidence Threshold\nDwell Time · Guardian Check\nUnknown Person Alert]
    CFG_TABS --> CFG_NOISE[Noise Settings\nEnable/Disable · Sensitivity\nDetection Duration]
    CFG_TABS --> CFG_WASTE[Waste Settings\nEnable/Disable · AI Confidence\nDwell Time · Collection Window]
    CFG_TABS --> CFG_SYSTEM[System Settings\nAlert Cooldown · Evidence Retention\nAuto-Dispatch · Email Alerts\nSMS Alerts]
    CFG_CURFEW & CFG_NOISE & CFG_WASTE & CFG_SYSTEM --> CFG_SAVE[Save Settings]
    CFG_SAVE --> CFG_APPLY[Settings Applied to\nAI Detection Pipeline]
    CFG_CURFEW & CFG_NOISE & CFG_WASTE & CFG_SYSTEM --> CFG_RESET[Reset to Defaults]
```

---

## 3. Dispatcher Web Dashboard — Full Flow

```mermaid
flowchart TD
    DISP_DASH([Dispatcher Dashboard]) --> SIDEBAR{Sidebar Navigation}

    SIDEBAR --> D_OVERVIEW[Overview / Dashboard]
    SIDEBAR --> D_CAMERAS[Cameras]
    SIDEBAR --> D_VIOLATIONS[Violations / Alerts]
    SIDEBAR --> D_RECORDS[Records]
    SIDEBAR --> D_RESLOG[Resident Log]

    %% Overview
    D_OVERVIEW --> D_KPI[KPI Cards:\nActive Violations · Cameras Online\nOfficers on Duty · Total Alerts]
    D_OVERVIEW --> D_FEEDS[Live Camera Feeds Preview]
    D_OVERVIEW --> D_RECENT[Recent Violations Feed]

    %% Cameras
    D_CAMERAS --> DC_GRID[Camera Grid\nOnline / Degraded / Offline Status]

    %% Violations — same capability as Admin
    D_VIOLATIONS --> DV_FILTER{Filter}
    DV_FILTER --> DV_ALL[All Active]
    DV_FILTER --> DV_ACTIVE[Active]
    DV_FILTER --> DV_DISP[Dispatched]

    DV_ALL & DV_ACTIVE & DV_DISP --> DV_CARD[Click Violation Card]
    DV_CARD --> DV_MODAL[Violation Detail Modal]

    DV_MODAL --> DVM_STATUS{Alert Status}

    DVM_STATUS -->|Active| DVM_ACT[Dismiss Alert\nDispatch Officers\nContact Guardian if Curfew]
    DVM_STATUS -->|Dispatched| DVM_DSP[Reassign Officers\nMark Resolved\nContact Guardian if Curfew]

    DVM_ACT --> DVM_DISPATCH[Dispatch Modal\nSelect Officers → Confirm]
    DVM_ACT --> DVM_DISMISS[Dismiss Modal\nSelect Reason → Confirm]
    DVM_DSP --> DVM_RESOLVE[Resolve Checklist\nAll Residents Listed\nPossible Candidate Highlighted\nConfirm & Resolve]
    DVM_DSP --> DVM_REASSIGN[Reassign Modal\nUpdate Officers]

    DVM_ACT & DVM_DSP --> DVM_GUARDIAN[Contact Guardian\nSMS to Household]

    %% Records
    D_RECORDS --> DR_FILTER[Filter: All / Resolved / Dismissed]
    DR_FILTER --> DR_SEARCH[Search Violations]
    DR_SEARCH --> DR_VIEW[View Closed Violation Details]

    %% Resident Log
    D_RESLOG --> DRL_LIST[All Tracked Residents]
    DRL_LIST --> DRL_SEARCH[Search by Name / ID]
    DRL_SEARCH --> DRL_PROFILE[Resident Profile\nTotal Violation Count\nViolation History]
    DRL_PROFILE --> DRL_DETAIL[View Violation Entry Details]
```

---

## 4. Officer Mobile App — Full Flow

```mermaid
flowchart TD
    OFFICER_APP([Officer App — Logged In]) --> TABS{Bottom Tab Bar}

    TABS --> TAB_ASSIGN[Assignments Tab\nShield Icon]
    TABS --> TAB_HISTORY[History Tab\nClock Icon]
    TABS --> TAB_PROFILE[Profile Tab\nUser Icon]

    %% ── Assignments Tab ──────────────────────────────────────────────────────
    TAB_ASSIGN --> AS_HEADER[Header: Officer Name\nPending Count Badge]
    AS_HEADER --> AS_FILTER{Filter}
    AS_FILTER --> AS_ALL[All\nActive + Dispatched]
    AS_FILTER --> AS_PENDING[Pending\nActive Only]
    AS_FILTER --> AS_ACCEPTED[Accepted\nDispatched Only]

    AS_ALL & AS_PENDING & AS_ACCEPTED --> AS_SECTIONS[Two Sections:\nMy Assignments\nOther Assignments]

    AS_SECTIONS --> AS_CARD[Tap Assignment Card]
    AS_CARD --> AS_DETAIL[Assignment Detail Screen]

    AS_DETAIL --> AD_INFO[Violation Info:\nType · Location · Time\nConfidence · Evidence Image\nDescription · Status]

    AD_INFO --> AD_STATUS{Assignment\nStatus}

    AD_STATUS -->|Pending / Active| AD_PENDING_ACTIONS[Pending Actions]
    AD_PENDING_ACTIONS --> AD_ACCEPT[Accept Assignment\nStatus → Dispatched\nOfficer confirmed on scene]
    AD_PENDING_ACTIONS --> AD_DISMISS_ACT[Dismiss Alert]
    AD_DISMISS_ACT --> AD_DISMISS_MODAL[Dismiss Modal\nSelect Reason\nOptional Custom Note]
    AD_DISMISS_MODAL --> AD_DISMISS_CONFIRM[Confirm\nStatus → Acknowledged]

    AD_STATUS -->|Accepted / Dispatched| AD_ACCEPTED_ACTIONS[Accepted Actions]
    AD_ACCEPTED_ACTIONS --> AD_RESOLVE[Mark as Resolved\nStatus → Resolved]
    AD_ACCEPTED_ACTIONS --> AD_DISMISS_ACT2[Dismiss Alert]
    AD_DISMISS_ACT2 --> AD_DISMISS_MODAL

    AD_ACCEPT --> AS_REFRESH[Assignments List Refreshed\nPull to Refresh Available]
    AD_RESOLVE --> AS_REFRESH
    AD_DISMISS_CONFIRM --> AS_REFRESH

    %% ── History Tab ──────────────────────────────────────────────────────────
    TAB_HISTORY --> HIS_COUNT[Header: Total Closed Count]
    HIS_COUNT --> HIS_SECTIONS[Two Sections]
    HIS_SECTIONS --> HIS_RESOLVED[Resolved Assignments]
    HIS_SECTIONS --> HIS_DISMISSED[Dismissed Assignments]
    HIS_RESOLVED & HIS_DISMISSED --> HIS_CARD[Tap Record Card]
    HIS_CARD --> HIS_DETAIL[View Closed Assignment Details\nRead-only]

    %% ── Profile Tab ──────────────────────────────────────────────────────────
    TAB_PROFILE --> PRO_INFO[Officer Profile Info:\nFull Name · Badge Number\nLocation · Email · Phone]
    PRO_INFO --> PRO_STATS[Assignment Stats:\nTotal · Completed · Pending]

    PRO_INFO --> PRO_STATUS[Set Duty Status]
    PRO_STATUS --> ST_ONDUTY[On Duty\nAvailable for Dispatch]
    PRO_STATUS --> ST_ONCALL[On Call / Responding\nCurrently Engaged]
    PRO_STATUS --> ST_OFFDUTY[Off Duty\nUnavailable — Hidden in Dispatch]

    PRO_INFO --> PRO_CHGPW[Change Password]
    PRO_CHGPW --> PW_FORM[Enter Current + New Password]
    PW_FORM --> PW_CONFIRM[Password Updated]

    PRO_INFO --> PRO_LOGOUT[Logout]
    PRO_LOGOUT --> MOB_LOGIN([Back to Login Screen])
```

---

## 5. AI Detection Pipeline — Background Flow

```mermaid
flowchart TD
    AI_START([AI Detection Running\nin Background]) --> DET_LOOP[Continuous Webcam Frame Loop]

    DET_LOOP --> YOLO[YOLOv8 — Detect Persons in Frame]
    YOLO --> YOLO_FOUND{Person\nDetected?}
    YOLO_FOUND -->|No| DET_LOOP
    YOLO_FOUND -->|Yes| FACE[InsightFace ArcFace\nExtract Face Embedding]

    FACE --> MATCH[Match vs face_db.json\nCosine Similarity Score]
    MATCH --> MATCH_FOUND{Match Above\nConfidence Threshold?}
    MATCH_FOUND -->|No| DET_LOOP
    MATCH_FOUND -->|Yes| AGE_CHK{Person Age\nBelow Curfew Age?}
    AGE_CHK -->|No| DET_LOOP
    AGE_CHK -->|Yes| TIME_CHK{Within Curfew\nHours?}
    TIME_CHK -->|No| DET_LOOP
    TIME_CHK -->|Yes| DWELL[Dwell Timer\nConfirm presence for N seconds]
    DWELL --> DWELL_CHK{Dwell Time\nMet?}
    DWELL_CHK -->|No| DET_LOOP
    DWELL_CHK -->|Yes| COOLDOWN{Alert Cooldown\nPassed?}
    COOLDOWN -->|No| DET_LOOP
    COOLDOWN -->|Yes| ALERT_CREATE[Create Alert Record\nSave Evidence Image\nSet Suspect Name from Face Match]

    ALERT_CREATE --> NOTIF[Alert Appears on\nWeb Dashboard in Real-time]
    NOTIF --> GUARDIAN_CHK{guardian_check\nEnabled in Settings?}
    GUARDIAN_CHK -->|Yes| AUTO_SMS[Trigger SMS to\nGuardian if configured]
    GUARDIAN_CHK -->|No| NOTIF_END[Dispatcher / Admin\nReviews Alert Manually]
    AUTO_SMS --> NOTIF_END

    ALERT_CREATE --> OFF_POLL[Officer App Polls Every 4s\nNew Assignment Appears]
```

---

## Role & Page Access Summary

| Page / Feature | Admin | Dispatcher | Both | Officer (web) | Officer (mobile) |
|---|:---:|:---:|:---:|:---:|:---:|
| Overview Dashboard | ✅ | ✅ | ✅ | ❌ | ❌ |
| Live Cameras | ✅ | ✅ | ✅ | ✅ | ❌ |
| Violations / Alerts | ✅ | ✅ | ✅ | ✅ | ✅ (own) |
| Records | ✅ | ✅ | ✅ | ✅ | ❌ |
| Resident Log | ✅ | ✅ | ❌ | ❌ | ❌ |
| Resident Database | ✅ | ❌ | ❌ | ❌ | ❌ |
| Officers & Personnel | ✅ | ❌ | ❌ | ❌ | ❌ |
| System Settings | ✅ | ❌ | ❌ | ❌ | ❌ |
| Dispatch Officers | ✅ | ✅ | ✅ | ❌ | ❌ |
| Contact Guardian SMS | ✅ | ✅ | ✅ | ❌ | ❌ |
| Assignments (mobile) | ❌ | ❌ | ❌ | ❌ | ✅ |
| History (mobile) | ❌ | ❌ | ❌ | ❌ | ✅ |
| Duty Status Toggle | ❌ | ❌ | ❌ | ❌ | ✅ |
| Face Enrollment | ✅ | ❌ | ❌ | ❌ | ❌ |
| Register Officers | ✅ | ❌ | ❌ | ❌ | ❌ |
| Register Dispatchers | ✅ | ❌ | ❌ | ❌ | ❌ |
