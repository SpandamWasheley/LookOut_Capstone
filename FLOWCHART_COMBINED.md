# LookOut System — Separate Flowcharts

---

## Diagram 1 — Authentication Flow

```mermaid
flowchart TD
    START([User Opens LookOut System]) --> PORTAL{Which Portal?}
    PORTAL -->|Web Browser| WEB_LOGIN
    PORTAL -->|Mobile App| MOB_LOGIN

    subgraph WEB_AUTH [Web Authentication]
        WEB_LOGIN[Web Login Page\nUsername + Password]
        WEB_LOGIN --> WEB_CHK{Authenticate}
        WEB_CHK -->|Invalid Credentials| WEB_ERR[Show Error Message]
        WEB_ERR --> WEB_LOGIN
        WEB_CHK -->|Forgot Password| WFP[Forgot Password Page]
        WFP --> WFP_EMAIL[Enter Registered Email]
        WFP_EMAIL --> WFP_OTP[Enter OTP Code Sent to Email]
        WFP_OTP --> WFP_RESET[Set New Password]
        WFP_RESET --> WEB_LOGIN
        WEB_CHK -->|Officer Account on Web| WEB_DENY[Access Denied\nOfficer Accounts Use Mobile App Only]
        WEB_CHK -->|Valid - Admin| ADM_PW{Must Change Password?}
        WEB_CHK -->|Valid - Dispatcher| DSP_PW{Must Change Password?}
        WEB_CHK -->|Valid - Both Role| BTH_PW{Must Change Password?}
        ADM_PW -->|Yes| ADM_CHGPW[Force Change Password Page]
        ADM_CHGPW --> ADMIN_DASH([Go to Admin Dashboard])
        ADM_PW -->|No| ADMIN_DASH
        DSP_PW -->|Yes| DSP_CHGPW[Force Change Password Page]
        DSP_CHGPW --> DISP_DASH([Go to Dispatcher Dashboard])
        DSP_PW -->|No| DISP_DASH
        BTH_PW -->|Yes| BTH_CHGPW[Force Change Password Page]
        BTH_CHGPW --> BOTH_DASH([Go to Both-Role Dashboard])
        BTH_PW -->|No| BOTH_DASH
    end

    subgraph MOB_AUTH [Mobile Authentication]
        MOB_LOGIN[Officer App Login\nUsername + Password]
        MOB_LOGIN --> MOB_CHK{Authenticate\nOfficer Accounts Only}
        MOB_CHK -->|Invalid Credentials| MOB_ERR[Show Error Message]
        MOB_ERR --> MOB_LOGIN
        MOB_CHK -->|Forgot Password| MFP[Forgot Password Screen]
        MFP --> MFP_EMAIL[Enter Email]
        MFP_EMAIL --> MFP_OTP[Enter OTP Code]
        MFP_OTP --> MFP_RESET[Set New Password]
        MFP_RESET --> MOB_LOGIN
        MOB_CHK -->|Valid| MOB_PW{Must Change Password?}
        MOB_PW -->|Yes| MOB_CHGPW[Change Password Screen]
        MOB_CHGPW --> OFFICER_APP([Go to Officer App Tabs])
        MOB_PW -->|No| OFFICER_APP
    end

    WEB_DENY --> END1([END])
    ADMIN_DASH --> END2([END - Enter Admin Dashboard])
    DISP_DASH --> END3([END - Enter Dispatcher Dashboard])
    BOTH_DASH --> END4([END - Enter Both-Role Dashboard])
    OFFICER_APP --> END5([END - Enter Officer App])
```

---

## Diagram 2 — Admin Web Dashboard

```mermaid
flowchart TD
    ADMIN_START([Admin Dashboard]) --> A_NAV{Sidebar Navigation}

    A_NAV --> A_OV[Overview]
    A_NAV --> A_LIVEFEEDS[Live Feeds]
    A_NAV --> A_VIO[Violations]
    A_NAV --> A_REC[Records]
    A_NAV --> A_RLOG[Resident Log]
    A_NAV --> A_RESI[Resident Database]
    A_NAV --> A_OFF[Officers and Personnel]
    A_NAV --> A_CFG[System Settings]

    subgraph A_OVERVIEW [Overview Page]
        A_OV --> A_KPI[KPI Cards\nActive Violations - Cameras Online\nOfficers on Duty - Total Alerts]
        A_OV --> A_FEEDPREV[Live Feeds Preview Panel\nShows Camera Thumbnails]
        A_OV --> A_RECENT[Recent Violations Panel]
        A_FEEDPREV --> A_VIEWALL{View All Button Clicked?}
        A_VIEWALL -->|Yes| A_LIVEFEEDS
        A_VIEWALL -->|No| A_FEEDPREV
    end

    subgraph A_LIVEFEEDS_PAGE [Live Feeds Page]
        A_LIVEFEEDS --> A_CGRID[Full Camera Grid\nAll Registered Cameras]
        A_CGRID --> A_CSTATUS{Camera Status}
        A_CSTATUS --> A_CONLINE[Online - Live Feed Visible]
        A_CSTATUS --> A_CDEG[Degraded - Partial Feed]
        A_CSTATUS --> A_COFF[Offline - No Feed]
    end

    subgraph A_VIOLATIONS_PAGE [Violations Page]
        A_VIO --> A_VFIL{Filter Tab}
        A_VFIL --> A_VALL[All Active]
        A_VFIL --> A_VACT[Active Only]
        A_VFIL --> A_VDSP[Dispatched Only]
        A_VALL & A_VACT & A_VDSP --> A_VCARD[Click Violation Card]
        A_VCARD --> A_VMOD[Violation Detail Modal\nEvidence - Location - Time\nConfidence - Officers Assigned]
        A_VMOD --> A_VSTATUS{Alert Status}

        A_VSTATUS -->|Active| A_VACT_A[Active Actions]
        A_VACT_A --> A_DISMISS[Dismiss Alert]
        A_VACT_A --> A_DISPATCH[Dispatch Officers]
        A_VACT_A --> A_CG1{Curfew Violation?}
        A_CG1 -->|Yes| A_GUARDIAN[Contact Guardian SMS]

        A_VSTATUS -->|Dispatched| A_VDSP_A[Dispatched Actions]
        A_VDSP_A --> A_REASSIGN[Reassign Officers]
        A_VDSP_A --> A_RESOLVE[Mark as Resolved]
        A_VDSP_A --> A_CG2{Curfew Violation?}
        A_CG2 -->|Yes| A_GUARDIAN

        A_DISMISS --> A_DMOD[Dismiss Modal\nSelect Reason + Optional Notes]
        A_DMOD --> A_DCONFIRM[Status becomes Acknowledged]

        A_DISPATCH --> A_DPMOD[Dispatch Modal\nSelect One or More Officers]
        A_DPMOD --> A_DPCONFIRM[Status becomes Dispatched\nOfficers Notified on App]

        A_RESOLVE --> A_RCHK[Resolve Checklist Modal\nAll Residents Listed\nFace Match Labeled Possible Candidate]
        A_RCHK --> A_RSEL[Select Involved Residents]
        A_RSEL --> A_RCONFIRM[Status becomes Resolved\nLinked to Resident Log]

        A_GUARDIAN --> A_GHHS[Search Households]
        A_GHHS --> A_GSEL[Select Phone Numbers to Notify]
        A_GSEL --> A_GMSG[Edit SMS Message]
        A_GMSG --> A_GSEND[Send SMS via Semaphore]
    end

    subgraph A_RECORDS_PAGE [Records Page]
        A_REC --> A_RFIL{Filter}
        A_RFIL --> A_RALL[All Closed]
        A_RFIL --> A_RRES[Resolved Only]
        A_RFIL --> A_RDIS[Dismissed Only]
        A_RALL & A_RRES & A_RDIS --> A_RSEARCH[Search by Type or Officer Name]
        A_RSEARCH --> A_RCARD[Click Record]
        A_RCARD --> A_RDETAIL[View Full Violation Details\nRead-only]
    end

    subgraph A_RESLOG_PAGE [Resident Log Page]
        A_RLOG --> A_RLLIST[All Tracked Residents List]
        A_RLLIST --> A_RLSEARCH[Search by Name or Barangay ID]
        A_RLSEARCH --> A_RLPERSON[Select Resident]
        A_RLPERSON --> A_RLPROFILE[Resident Violation Profile\nTotal Violation Count\nFull Violation History]
        A_RLPROFILE --> A_RLVIO[Click Violation Entry\nView Full Details]
    end

    subgraph A_RESIDENTS_PAGE [Resident Database Page]
        A_RESI --> A_RVIEW{View Mode}
        A_RVIEW --> A_RHHVIEW[Household List View]
        A_RVIEW --> A_RTABLE[Resident Table View]

        A_RHHVIEW --> A_ADDHH[Add Household Button]
        A_ADDHH --> A_HHMOD[Household Form\nFamily Name - Address - Purok\nZone - Contact - Enrolled Date]
        A_HHMOD --> A_HHSAVE[Household Saved]

        A_RHHVIEW --> A_HHEXP[Expand Household Card]
        A_HHEXP --> A_HMLIST[View Members List]
        A_HMLIST --> A_MEMADD[Add Member]
        A_MEMADD --> A_MEMMOD[Member Form\nName - Birthdate - Relation\nBarangay ID - Phone - Status]
        A_MEMMOD --> A_MEMSAVE[Member Saved]
        A_HMLIST --> A_MEMEDIT[Edit Member Info]
        A_HMLIST --> A_MEMENROLL[Enroll Face Button]
        A_MEMENROLL --> A_ENROLLMOD[Enrollment Modal\nUpload or Capture Face Photo]
        A_ENROLLMOD --> A_ENROLLSAVE[Face Enrolled in face_db\nUsed by Curfew AI Detection]
    end

    subgraph A_OFFICERS_PAGE [Officers and Personnel Page]
        A_OFF --> A_OTAB{Select Tab}
        A_OTAB --> A_OFFLIST[Officers List\nName - Badge - Status - Location]
        A_OTAB --> A_DISPLIST[Dispatchers List\nUsername - Email - Role]

        A_OFFLIST --> A_OFFREG[Register New Officer or Both Role]
        A_OFFREG --> A_OFFEMAIL[Enter Officer Email Address]
        A_OFFEMAIL --> A_OFFCODE[System Sends Verification Code to Email]
        A_OFFCODE --> A_OFFVERIFY[Admin Enters Code to Verify]
        A_OFFVERIFY --> A_OFFFORM[Fill Officer Details\nName - Badge - Location - Phone\nUsername - Password]
        A_OFFFORM --> A_OFFSAVE[Officer Account Created\nOfficer Logs In via Mobile App]
        A_OFFLIST --> A_OFFEDIT[Edit Officer Info\nStatus - Location - Badge]
        A_OFFLIST --> A_OFFDEL[Delete Officer Account]

        A_DISPLIST --> A_DISPAREG[Register Dispatcher]
        A_DISPAREG --> A_DISPAFORM[Fill Details\nName - Email - Username - Password - Role]
        A_DISPAFORM --> A_DISPASAVE[Dispatcher Account Created\nLogs In via Web Dashboard]
        A_DISPLIST --> A_DISPADEL[Delete Dispatcher Account]
    end

    subgraph A_SETTINGS_PAGE [System Settings Page]
        A_CFG --> A_CSEC{Settings Section}
        A_CSEC --> A_CCURFEW[Curfew Settings\nCurfew Hours - Age Limit\nAI Confidence Threshold\nGuardian Check - Unknown Person Alert]
        A_CSEC --> A_CNOISE[Noise Settings\nEnable or Disable\nNoise Threshold dB - Duration]
        A_CSEC --> A_CWASTE[Waste Settings\nEnable or Disable\nAI Confidence - Dwell Time\nCollection Window Hours]
        A_CSEC --> A_CSYSTEM[System Settings\nEvidence Retention Days\nAuto-Dispatch - Email Alerts - SMS Alerts]
        A_CCURFEW & A_CNOISE & A_CWASTE & A_CSYSTEM --> A_CSAVE[Save Settings]
        A_CSAVE --> A_CAPPLY[Settings Applied to Detection Pipelines]
        A_CCURFEW & A_CNOISE & A_CWASTE & A_CSYSTEM --> A_CRESET[Reset to Defaults]
    end

    A_NAV --> A_LOGOUT[Logout]
    A_LOGOUT --> END_ADMIN([END - Return to Web Login])
```

---

## Diagram 3 — Dispatcher Web Dashboard

```mermaid
flowchart TD
    DISP_START([Dispatcher Dashboard]) --> D_NAV{Sidebar Navigation}

    D_NAV --> D_OV[Overview]
    D_NAV --> D_LIVEFEEDS[Live Feeds]
    D_NAV --> D_VIO[Violations]
    D_NAV --> D_REC[Records]
    D_NAV --> D_RLOG[Resident Log]

    subgraph D_OVERVIEW [Overview Page]
        D_OV --> D_KPI[KPI Cards\nActive Violations - Cameras Online\nOfficers on Duty - Total Alerts]
        D_OV --> D_FEEDPREV[Live Feeds Preview Panel\nShows Camera Thumbnails]
        D_OV --> D_RECENT[Recent Violations Panel]
        D_FEEDPREV --> D_VIEWALL{View All Button Clicked?}
        D_VIEWALL -->|Yes| D_LIVEFEEDS
        D_VIEWALL -->|No| D_FEEDPREV
    end

    subgraph D_LIVEFEEDS_PAGE [Live Feeds Page]
        D_LIVEFEEDS --> D_CGRID[Full Camera Grid\nAll Registered Cameras]
        D_CGRID --> D_CSTATUS{Camera Status}
        D_CSTATUS --> D_CONLINE[Online - Live Feed Visible]
        D_CSTATUS --> D_CDEG[Degraded - Partial Feed]
        D_CSTATUS --> D_COFF[Offline - No Feed]
    end

    subgraph D_VIOLATIONS_PAGE [Violations Page]
        D_VIO --> D_VFIL{Filter Tab}
        D_VFIL --> D_VALL[All Active]
        D_VFIL --> D_VACT[Active Only]
        D_VFIL --> D_VDSP[Dispatched Only]
        D_VALL & D_VACT & D_VDSP --> D_VCARD[Click Violation Card]
        D_VCARD --> D_VMOD[Violation Detail Modal\nEvidence - Location - Time\nConfidence - Officers Assigned]
        D_VMOD --> D_VSTATUS{Alert Status}

        D_VSTATUS -->|Active| D_ACT[Active Actions]
        D_ACT --> D_DISMISS[Dismiss Alert]
        D_ACT --> D_DISPATCH[Dispatch Officers]
        D_ACT --> D_CG1{Curfew Violation?}
        D_CG1 -->|Yes| D_GUARDIAN[Contact Guardian SMS]

        D_VSTATUS -->|Dispatched| D_DSP[Dispatched Actions]
        D_DSP --> D_REASSIGN[Reassign Officers]
        D_DSP --> D_RESOLVE[Mark as Resolved]
        D_DSP --> D_CG2{Curfew Violation?}
        D_CG2 -->|Yes| D_GUARDIAN

        D_DISMISS --> D_DMOD[Dismiss Modal\nSelect Reason + Notes]
        D_DMOD --> D_DCONFIRM[Status becomes Acknowledged]

        D_DISPATCH --> D_DPMOD[Dispatch Modal\nSelect Officers]
        D_DPMOD --> D_DPCONFIRM[Status becomes Dispatched\nOfficers Notified on App]

        D_RESOLVE --> D_RCHK[Resolve Checklist Modal\nAll Residents Listed\nFace Match Labeled Possible Candidate]
        D_RCHK --> D_RSEL[Select Involved Residents]
        D_RSEL --> D_RCONFIRM[Status becomes Resolved\nLinked to Resident Log]

        D_GUARDIAN --> D_GHHS[Search Households]
        D_GHHS --> D_GSEL[Select Phone Numbers]
        D_GSEL --> D_GMSG[Edit SMS Message]
        D_GMSG --> D_GSEND[Send SMS via Semaphore]
    end

    subgraph D_RECORDS_PAGE [Records Page]
        D_REC --> D_RFIL{Filter}
        D_RFIL --> D_RALL[All Closed]
        D_RFIL --> D_RRES[Resolved Only]
        D_RFIL --> D_RDIS[Dismissed Only]
        D_RALL & D_RRES & D_RDIS --> D_RSEARCH[Search by Type or Name]
        D_RSEARCH --> D_RCARD[Click Record]
        D_RCARD --> D_RDETAIL[View Full Violation Details\nRead-only]
    end

    subgraph D_RESLOG_PAGE [Resident Log Page]
        D_RLOG --> D_RLLIST[All Tracked Residents]
        D_RLLIST --> D_RLSEARCH[Search by Name or Barangay ID]
        D_RLSEARCH --> D_RLPERSON[Select Resident]
        D_RLPERSON --> D_RLPROFILE[Resident Profile\nTotal Violation Count\nFull Violation History]
        D_RLPROFILE --> D_RLVIO[Click Violation Entry - View Details]
    end

    D_NAV --> D_LOGOUT[Logout]
    D_LOGOUT --> END_DISP([END - Return to Web Login])
```

---

## Diagram 4 — Officer Mobile App

```mermaid
flowchart TD
    APP_START([Officer App - Logged In]) --> M_TABS{Bottom Tab Bar}

    M_TABS --> M_ASSIGN[Assignments Tab]
    M_TABS --> M_HISTORY[History Tab]
    M_TABS --> M_PROFILE[Profile Tab]

    subgraph M_ASSIGNMENTS [Assignments Tab]
        M_ASSIGN --> M_HEADER[Header: Officer Name - Pending Count Badge]
        M_HEADER --> M_FILTER{Filter}
        M_FILTER --> M_FALL[All - Active and Dispatched]
        M_FILTER --> M_FPEND[Pending - Active Only]
        M_FILTER --> M_FACC[Accepted - Dispatched Only]

        M_FALL & M_FPEND & M_FACC --> M_LIST[My Assigned Violations Only\nOnly violations assigned to this officer are shown]
        M_LIST --> M_EMPTY{Any Assignments?}
        M_EMPTY -->|No| M_NONE[No Assignments Screen\nWaiting for Dispatch]
        M_EMPTY -->|Yes| M_CARD[Tap Assignment Card]

        M_CARD --> M_DETAIL[Assignment Detail Screen\nViolation Type - Location - Time\nConfidence Score - Evidence Image\nDescription - Current Status]

        M_DETAIL --> M_STATUS{Current Status}

        M_STATUS -->|Pending / Active| M_PACT[Pending Actions]
        M_PACT --> M_ACCEPT[Accept Assignment\nStatus becomes Dispatched\nOfficer Confirmed Responding]
        M_PACT --> M_PDISMISS[Dismiss Alert]

        M_STATUS -->|Accepted / Dispatched| M_DACT[Accepted Actions]
        M_DACT --> M_RESOLVE[Mark as Resolved\nStatus becomes Resolved]
        M_DACT --> M_ADISMISS[Dismiss Alert]

        M_PDISMISS & M_ADISMISS --> M_DMOD[Dismiss Modal\nSelect Preset Reason\nOptional Custom Note]
        M_DMOD --> M_DCONFIRM[Confirmed\nStatus becomes Acknowledged]

        M_ACCEPT & M_RESOLVE & M_DCONFIRM --> M_REFRESH[Assignment List Refreshes\nPull to Refresh Also Available]
    end

    subgraph M_HIST [History Tab]
        M_HISTORY --> M_HHEADER[Header - Total Closed by This Officer]
        M_HHEADER --> M_HSEC[Two Sections]
        M_HSEC --> M_HRES[Resolved by This Officer]
        M_HSEC --> M_HDIS[Dismissed by This Officer]
        M_HRES & M_HDIS --> M_HEMPTY{Any History?}
        M_HEMPTY -->|No| M_HNO[No History Yet Screen]
        M_HEMPTY -->|Yes| M_HCARD[Tap History Card]
        M_HCARD --> M_HDETAIL[View Closed Violation Details\nRead-only - Full Info]
    end

    subgraph M_PROF [Profile Tab]
        M_PROFILE --> M_PINFO[Officer Profile Card\nFull Name - Badge Number\nLocation - Email - Phone]
        M_PINFO --> M_PSTATS[My Assignment Stats\nTotal Assigned - Completed - Pending]
        M_PINFO --> M_PSTATUS[Set My Duty Status]
        M_PSTATUS --> M_SONDUTY[On Duty\nVisible and Available for Dispatch]
        M_PSTATUS --> M_SONCALL[On Call / Responding\nCurrently Engaged]
        M_PSTATUS --> M_SOFFDUTY[Off Duty\nHidden from Dispatcher View]
        M_PINFO --> M_PCHGPW[Change Password]
        M_PCHGPW --> M_PWFORM[Enter Current Password\nEnter New Password\nConfirm New Password]
        M_PWFORM --> M_PWSAVE[Password Updated]
        M_PINFO --> M_PLOGOUT[Logout Button]
        M_PLOGOUT --> END_MOB([END - Return to Login Screen])
    end

    M_NONE --> END_WAIT([END - Waiting for Dispatch])
    M_REFRESH --> END_REFRESH([END - List Updated])
    M_HDETAIL --> END_HIST([END - View Complete])
    M_PWSAVE --> END_PW([END - Password Changed])
```

---

## Diagram 5 — AI Curfew Detection Pipeline

```mermaid
flowchart TD
    CURFEW_START([Curfew Detection Service Started\npython manage.py watch_curfew]) --> CURFEW_INIT[Load face_db.json\nPrecompute Face Embeddings]
    CURFEW_INIT --> CURFEW_CAMCHK{Webcam\nAvailable?}
    CURFEW_CAMCHK -->|No| CURFEW_CAMFAIL[Error: Cannot Open Webcam]
    CURFEW_CAMFAIL --> END_CAMFAIL([END - Service Stopped])
    CURFEW_CAMCHK -->|Yes| CURFEW_LOOP[Read Webcam Frame]

    CURFEW_LOOP --> CURFEW_FRAMEOK{Frame\nRead OK?}
    CURFEW_FRAMEOK -->|No| CURFEW_LOOP
    CURFEW_FRAMEOK -->|Yes| CURFEW_SETTINGS[Reload SystemSettings\nevery 5 seconds]

    CURFEW_SETTINGS --> CURFEW_YOLO[YOLOv8\nDetect Persons in Frame]
    CURFEW_YOLO --> CURFEW_YFOUND{Person\nDetected?}
    CURFEW_YFOUND -->|No| CURFEW_LOOP
    CURFEW_YFOUND -->|Yes| CURFEW_CROP[Crop Person Region]
    CURFEW_CROP --> CURFEW_FACE[InsightFace ArcFace\nExtract 512-d Face Embedding]
    CURFEW_FACE --> CURFEW_EMBCHK{Face\nDetected in Crop?}
    CURFEW_EMBCHK -->|No| CURFEW_LOOP
    CURFEW_EMBCHK -->|Yes| CURFEW_MATCH[Cosine Similarity Match\nvs Precomputed face_db]
    CURFEW_MATCH --> CURFEW_MFOUND{Match Score Above\nConfidence Threshold?}
    CURFEW_MFOUND -->|No| CURFEW_LOOP
    CURFEW_MFOUND -->|Yes| CURFEW_AGE{Matched Person Age\nBelow Curfew Age Limit?}
    CURFEW_AGE -->|No - Adult| CURFEW_LOOP
    CURFEW_AGE -->|Yes - Minor| CURFEW_TIME{Current Time\nWithin Curfew Hours?}
    CURFEW_TIME -->|No - Outside Curfew| CURFEW_LOOP
    CURFEW_TIME -->|Yes - Curfew Active| CURFEW_SAVE[Save Evidence Frame as JPG\nGenerate Image URL]
    CURFEW_SAVE --> CURFEW_ALERT[Create Alert Record in DB\nType: Curfew Violation\nStatus: Active\nSuspect: Matched Person Name\nConfidence: Match Score]
    CURFEW_ALERT --> CURFEW_NOTIF[Alert Appears on Web Dashboard\nReal-time 4-second Polling]
    CURFEW_NOTIF --> CURFEW_OFFICER[Officer App Receives Assignment\nPolled Every 4 Seconds]
    CURFEW_NOTIF --> CURFEW_GCCHK{guardian_check\nEnabled in Settings?}
    CURFEW_GCCHK -->|Yes| CURFEW_SMS[Auto-Notify Guardian\nSMS via Semaphore]
    CURFEW_GCCHK -->|No| CURFEW_MANUAL[Awaits Manual Action\nby Dispatcher or Admin]
    CURFEW_SMS --> CURFEW_MANUAL
    CURFEW_MANUAL --> CURFEW_LOOP

    CURFEW_LOOP --> CURFEW_STOP{Ctrl+C\nPressed?}
    CURFEW_STOP -->|No| CURFEW_LOOP
    CURFEW_STOP -->|Yes| END_CURFEW([END - Service Stopped])
```

---

## Diagram 6 — AI Garbage / Waste Detection Pipeline

```mermaid
flowchart TD
    WASTE_START([Waste Detection Service Started]) --> WASTE_INIT[Load YOLOv8 Waste Model\nLoad SystemSettings]
    WASTE_INIT --> WASTE_CAMCHK{Camera\nAvailable?}
    WASTE_CAMCHK -->|No| WASTE_CAMFAIL[Error: Cannot Open Camera]
    WASTE_CAMFAIL --> END_WCAMFAIL([END - Service Stopped])
    WASTE_CAMCHK -->|Yes| WASTE_ENACHK{Waste Detection\nEnabled in Settings?}
    WASTE_ENACHK -->|No| WASTE_SKIP[Detection Skipped\nCheck Again After Delay]
    WASTE_SKIP --> WASTE_ENACHK
    WASTE_ENACHK -->|Yes| WASTE_LOOP[Read Camera Frame]

    WASTE_LOOP --> WASTE_FRAMEOK{Frame\nRead OK?}
    WASTE_FRAMEOK -->|No| WASTE_LOOP
    WASTE_FRAMEOK -->|Yes| WASTE_SETTINGS[Reload SystemSettings\nevery 5 seconds]
    WASTE_SETTINGS --> WASTE_COLLECT{Within Waste\nCollection Window?}
    WASTE_COLLECT -->|Yes - Collection Ongoing| WASTE_LOOP
    WASTE_COLLECT -->|No - Outside Collection Hours| WASTE_YOLO[YOLOv8\nDetect Waste or Garbage in Frame]

    WASTE_YOLO --> WASTE_YFOUND{Waste\nDetected?}
    WASTE_YFOUND -->|No| WASTE_LOOP
    WASTE_YFOUND -->|Yes| WASTE_CONF{Detection Confidence\nAbove Threshold?}
    WASTE_CONF -->|No| WASTE_LOOP
    WASTE_CONF -->|Yes| WASTE_DWELL[Start Dwell Timer\nConfirm Waste Remains in Frame]
    WASTE_DWELL --> WASTE_DWCHK{Waste Present\nfor Required Dwell Duration?}
    WASTE_DWCHK -->|No - Waste Disappeared| WASTE_LOOP
    WASTE_DWCHK -->|Yes - Confirmed Dumping| WASTE_SAVE[Save Evidence Frame as JPG\nGenerate Image URL]
    WASTE_SAVE --> WASTE_ALERT[Create Alert Record in DB\nType: Illegal Waste Dumping\nStatus: Active\nConfidence: Detection Score]
    WASTE_ALERT --> WASTE_NOTIF[Alert Appears on Web Dashboard\nReal-time 4-second Polling]
    WASTE_NOTIF --> WASTE_OFFICER[Officer App Receives Assignment\nPolled Every 4 Seconds]
    WASTE_NOTIF --> WASTE_MANUAL[Awaits Manual Action\nby Dispatcher or Admin]
    WASTE_MANUAL --> WASTE_LOOP

    WASTE_LOOP --> WASTE_STOP{Service\nStopped?}
    WASTE_STOP -->|No| WASTE_LOOP
    WASTE_STOP -->|Yes| END_WASTE([END - Service Stopped])
```

---

## Diagram 7 — AI Noise Detection Pipeline

```mermaid
flowchart TD
    NOISE_START([Noise Detection Service Started]) --> NOISE_INIT[Initialize Audio Sensor\nLoad SystemSettings]
    NOISE_INIT --> NOISE_MICCHK{Audio Input\nAvailable?}
    NOISE_MICCHK -->|No| NOISE_MICFAIL[Error: Cannot Access Audio Input]
    NOISE_MICFAIL --> END_NFAIL([END - Service Stopped])
    NOISE_MICCHK -->|Yes| NOISE_ENACHK{Noise Detection\nEnabled in Settings?}
    NOISE_ENACHK -->|No| NOISE_SKIP[Detection Skipped\nCheck Again After Delay]
    NOISE_SKIP --> NOISE_ENACHK
    NOISE_ENACHK -->|Yes| NOISE_LOOP[Sample Audio Level\nContinuous Monitoring]

    NOISE_LOOP --> NOISE_SETTINGS[Reload SystemSettings\nevery 5 seconds]
    NOISE_SETTINGS --> NOISE_READ[Read Current Noise Level in dB]
    NOISE_READ --> NOISE_THRESH{Noise Level Above\nThreshold dB in Settings?}
    NOISE_THRESH -->|No - Normal Level| NOISE_LOOP
    NOISE_THRESH -->|Yes - Loud Noise| NOISE_DUR[Start Duration Timer\nConfirm Sustained Noise]
    NOISE_DUR --> NOISE_DURCHK{Noise Sustained\nfor Required Duration?}
    NOISE_DURCHK -->|No - Noise Stopped| NOISE_LOOP
    NOISE_DURCHK -->|Yes - Confirmed Violation| NOISE_SAVE[Save Audio Evidence Clip\nGenerate Evidence URL]
    NOISE_SAVE --> NOISE_ALERT[Create Alert Record in DB\nType: Noise Violation\nStatus: Active\nConfidence: dB Level Reading]
    NOISE_ALERT --> NOISE_NOTIF[Alert Appears on Web Dashboard\nReal-time 4-second Polling]
    NOISE_NOTIF --> NOISE_OFFICER[Officer App Receives Assignment\nPolled Every 4 Seconds]
    NOISE_NOTIF --> NOISE_MANUAL[Awaits Manual Action\nby Dispatcher or Admin]
    NOISE_MANUAL --> NOISE_LOOP

    NOISE_LOOP --> NOISE_STOP{Service\nStopped?}
    NOISE_STOP -->|No| NOISE_LOOP
    NOISE_STOP -->|Yes| END_NOISE([END - Service Stopped])
```
