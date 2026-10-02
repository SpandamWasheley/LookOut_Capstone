# LookOut — Indicators & Scoring Spec (v6.2)

*Every indicator, its points, how the status is decided, what the AI checker shows, and what the tanod sees.*

*Version 6.2, 3 October 2026 (alert details cleanup: see the change history below). Version 6.1, 2 October 2026 (Monitoring is no longer a panel on the Overview dashboard; it is viewed with the Include Monitoring filter on the Violations page. Nothing else changed from version 6). Version 6, 1 October 2026. Supersedes version 5. The AI-suggested status can now move one step up as well as down, with a stricter rule going up, and the smoking observations definition mentions smoke only when it is clearly visible. The official status still comes only from system indicators and is never changed by the AI. The code (core/vision/scoring.py, core/vision/vlm.py) and the alert UI must be updated to match; Section 14 lists the changes.*

# 1. The idea in plain words

The score works like a grading rubric. Each piece of evidence the system measures is a checkbox worth points. The object (bottle, cigarette, knife) is worth the most, because without it there is no violation. The points are added up, and the total decides the status the tanod sees.

Only measurable system evidence is scored: the object detector (YOLO), the pose model, the tracker, and the clock. The AI checker does no score calculation at all.

As soon as an object is detected for about 2 seconds, the event appears as Monitoring, so the tanod can watch it early. As more evidence adds up it rises to Possible and then Likely.

The AI checker (a vision-language model) is a second opinion. It looks at a short cropped clip and describes the context: seating, drinks set out, whether a knife is pointed at someone, whether it looks like ordinary work. This is shown in its own card, together with a suggested status. The official status never changes because of the AI.

One sentence for the panel: the system indicators decide the status; the AI checker shows context and a suggested status beside it, and the tanod makes the final call.

# 2. What the tanod sees

| **When**                                  | **Status** | **What the tanod sees**                                                          |
|-------------------------------------------|------------|----------------------------------------------------------------------------------|
| object not yet detected                   | Not shown  | Nothing. Behaviour points are still logged in the database.                      |
| object detected about 2 s, score under 55 | Monitoring | Listed quietly, no push notification. Viewable on the Violations page with the Include Monitoring filter. For proactive watching. |
| score 55 – 74                             | Possible   | Listed and notified, low priority                                                |
| score 75 and above                        | Likely     | Full alert with video evidence                                                   |

The app shows the status and a checklist of the evidence found, not the number. A score reads like a percentage, which it is not.

*Alert details — card layout*

| **Card**               | **Shows**                                                                                       | **Style**                     |
|------------------------|-------------------------------------------------------------------------------------------------|-------------------------------|
| Status                 | Monitoring / Possible / Likely only; the evidence checklist is behind a 'Details' toggle        | solid border — official       |
| Object confidence      | '78% conf' — how certain the YOLOv8 model was about the object ('—' when no object was detected) | solid border                  |
| AI context             | AI badge, observations sentence, answered fields; 'AI-generated · may be wrong'                 | dashed border, AI icon        |
| Status with AI context | the AI-suggested status, e.g. 'Possible → Likely', 'Likely → Possible', or 'Likely — AI agrees' | dashed border, AI icon        |
| Closed banner (dismissed / resolved) | who closed it, when, and why; plus a Timeline card (detected, status changes, assigned, closed)   | soft red / soft green          |

*ⓘ text for the Status card*

Shows how strongly the detected evidence points to a violation. It's based only on  
what the system detected (objects, movement, duration, and time), not on the AI.  
  
Monitoring: An object linked to a violation was detected. Watch the scene.  
Possible: Some signs of a violation, but not enough to be sure. Review the alert before acting.  
Likely: Strong evidence of a violation. Review and respond.

*ⓘ text for the Status with AI context card*

What the status would be if the AI's view of the scene were taken into account.  
This is only a suggestion. The official status does not change.  
  
If the AI is confident the scene is a violation, it may suggest one step higher.  
If it is confident the scene is ordinary activity (such as vending, selling, or  
eating), it may suggest one step lower. Review the clip to decide.

| **AI badge**                   | **When it shows**                                                                                                                      |
|--------------------------------|----------------------------------------------------------------------------------------------------------------------------------------|
| ✓ AI: supports this alert      | drinking_likelihood 'likely' or scene 'drinking_session'; hand_to_mouth 'smoking'; holdup_likelihood 'likely' or scene 'confrontation' |
| ⚠ AI: may be ordinary activity | scene_type or hand_to_mouth_activity is 'other_activity' (any confidence)                                                              |
| ? AI: unclear                  | anything else                                                                                                                          |
| AI context unavailable         | the AI checker failed or its reply was not valid JSON                                                                                  |

Statuses say 'Likely', not 'Confirmed'. The system proposes; the tanod confirms by marking the event Verified or Dismissed. That decision also provides labelled data for checking the thresholds.

Hysteresis: once Possible or Likely is entered, it is only left when the score falls 5 points below the threshold that admitted it. Rising is immediate. Monitoring ends when the object is no longer detected and the score decays.

# 3. How the status is decided

| **Step** | **What happens**                                                                                                             |
|----------|------------------------------------------------------------------------------------------------------------------------------|
| 1        | Add up the system indicators (YOLO, pose, tracking, clock)                                                                   |
| 2        | Holdup only: apply the time-block multiplier                                                                                 |
| 3        | Cap the score at 100                                                                                                         |
| 4        | Read the status: object detected → Monitoring; 55+ → Possible; 75+ → Likely                                                  |
| 5        | Puff-only smoking: cap the status at Possible                                                                                |
| 6        | Separately, the AI checker answers and the suggested status is worked out from Section 8's rule. It is shown, never applied. |

*Steps 1 to 5 produce the official status. Step 6 produces a second, display-only status. The stored score always reflects the measured evidence alone.*

# 4. The safety rules

| **Rule**                                 | **What it means**                                                                                                                                                                                                                                |
|------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| No object, no alert                      | Behaviour alone (standing, chatting, waiting) is never shown. The object must be detected for about 2 seconds, which starts Monitoring. The only exception is the puff-only smoking path (Section 6), which is capped at Possible.               |
| The AI checker does no scoring           | It adds no points and changes no status. Its answers and its suggested status are shown in separate cards.                                                                                                                                       |
| The AI suggestion moves one step at most | It can suggest one step lower when it is confident the scene is ordinary activity, or one step higher when it is confident the scene is a violation. The rule for going up is stricter, and puff-only smoking is never suggested above Possible. |
| Maximum is 100                           | The score is capped at 100.                                                                                                                                                                                                                      |

# 5. Public Drinking

| **Indicator**            | **Points** | **Source** | **Citation**           |
|--------------------------|------------|------------|------------------------|
| Bottle seen (2s+) — GATE | 40         | YOLO       | Omamalin (2022)        |
| Stayed 10+ minutes       | 15         | timer      | Omamalin               |
| Bottle at mouth \*       | 15         | pose       | arXiv 2507.00566       |
| Group of 2+ (stationary) | 10         | tracking   | Omamalin; Setti (2015) |
| Evening (4PM – midnight) | 5          | clock      | Thai ED (2021)         |
| **Total**                | **85**     |            |                        |

*\* Bottle at mouth scores only when the bottle is also detected. A hand near the face without a bottle is also eating, phoning or scratching.*

*A group with a bottle reaches Possible (70); Likely needs a bottle at the mouth as well (85). Drinking has no time multiplier: its time evidence is a band, so it adds points instead.*

# 6. Public Smoking

| **Indicator**                                     | **Points** | **Source**    | **Citation**              |
|---------------------------------------------------|------------|---------------|---------------------------|
| Smoking item detected (2s+) — GATE                | 40         | YOLO          | YOLOv8-MNC (2023)         |
| Hand going to mouth (1 puff)                      | 20         | pose          | keypoints + YOLOv8 (IEEE) |
| Did it again (2+ puffs)                           | 20         | pose          | PACT2.0; Sensors          |
| Repeated puff pattern (3+ puffs within 5 minutes) | 15         | pose          | citation pending          |
| Smoking item at the mouth \*                      | 15         | face + object | keypoints + YOLOv8        |
| **Total (capped at 100)**                         | **110**    |               |                           |

*\* Smoking item at the mouth scores only when the item is also detected.*

*Three paths*

| **Situation**                                            | **What happens**                                                                                                                                                                                        | **Max status** |
|----------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------|
| Cigarette detected                                       | Normal path. Item alone (2 s) = Monitoring. One puff is enough: item 40 + hand to mouth 20 = 60 (Possible); + item at mouth = 75 (Likely).                                                              | Likely         |
| No cigarette detected, one puff (typical walking smoker) | The hand-to-mouth triggers a high-resolution check: the cigarette detector runs on a close crop of that person's hands and face for the next few seconds. Found → normal path. Not found → logged only. | not shown      |
| No cigarette detected, 3+ puffs (lingering smoker)       | Puff-only path: 20 + 20 + 15 = 55. Shown as Possible, tagged 'No smoking item detected — based on hand movement only'. One or two puffs without the item are logged only, not Monitoring.               | Possible       |

*Why one puff without the object is not enough: a single hand to the mouth is also scratching, wiping sweat, covering a cough or eating. Repetition is what separates smoking from a one-off face touch. The 3-puff, 5-minute values are starting points to be checked on recorded clips.*

*Vapes have no YOLO class, so they can only be caught through the puff-only path. Whether vaping is covered depends on the wording of Ord. No. 532 (or RA 11900); confirm before claiming it.*

*There is no smoke-plume indicator: vision-language models fail on small smoke (SmokeBench), and gesture plus item is sufficient.*

# 7. Holdup

| **Indicator**                    | **Points** | **Code** | **Citation**              |
|----------------------------------|------------|----------|---------------------------|
| Knife / weapon present — GATE    | 45         | E14      | Fernandez-Testa (2024)    |
| Two people frozen close together | 20         | E12      | Ruiz-Santaquiteria (2021) |
| Someone loitering first          | 10         | E10      | citation pending          |
| **Total**                        | **75**     |          |                           |

*Time block (multiplier)*

| **Time block** | **Multiplier** | **Effect** |
|----------------|----------------|------------|
| 15:00 – 18:00  | x1.36          | stretches  |
| 12:00 – 15:00  | x1.31          | stretches  |
| 09:00 – 12:00  | x1.25          | stretches  |
| 18:00 – 21:00  | x1.09          | stretches  |
| 21:00 – 24:00  | x0.87          | shrinks    |
| 03:00 – 06:00  | x0.80          | shrinks    |
| 00:00 – 03:00  | x0.76          | shrinks    |
| 06:00 – 09:00  | x0.56          | shrinks    |

*Hard requirement: at least two people. A holdup needs a victim (Revised Penal Code, Article 293). This is a gate, not a scored indicator.*

*Knife filters: a knife box in the head/shoulder region, larger than a quarter of the person's height, or not near a wrist is rejected before scoring, and the detection must survive the vote and dwell windows.*

*Time source: Robielos & Duran (2020), five years of City of Manila robbery and theft records; each block's probability divided by 12.5%, the even spread across eight blocks. A bad hour can never create an alert by itself.*

*A knife alone always starts Monitoring, even when the time block shrinks its score (06:00–09:00: 45 x 0.56 = 25). A holdup at night (x0.87) with knife + two people frozen + loitering reaches 65 (Possible); the AI cannot raise it.*

# 8. The AI checker

| **Setting**                                | **Value**                                                                                                           |
|--------------------------------------------|---------------------------------------------------------------------------------------------------------------------|
| Model                                      | Qwen3-VL, run locally (being finalised: 2B with video input through Transformers, or 4B with frames through Ollama) |
| When it is called                          | When the event enters Monitoring (object detected about 2 seconds), and when a puff-only alert reaches Possible     |
| What it receives                           | The alert's evidence window, trimmed to the moment of detection and cropped to the alert's subject (table below)    |
| Frames                                     | 8–12, bunched around the detection or the puff rather than spread evenly                                            |
| What it changes                            | Nothing in the score or the official status. It produces the AI context card and the suggested status.              |
| What it shows                              | AI badge, the answered fields as a checklist, and the observations sentence, marked AI-generated                    |
| Faces blurred before sending               | No — frames never leave the device, and blurring would hide the mouth the smoking question needs                    |
| If it fails or the reply is not valid JSON | The AI cards show 'AI context unavailable'; the official status is unaffected                                       |

*What to crop for each violation*

| **Violation** | **Crop**                                                 | **Why**                                                                                                                      |
|---------------|----------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------|
| Smoking       | Half body (head, shoulders, hands) of the alert's person | The action happens at the face; the rest wastes pixels. Tested: full frame answered 'unclear', half body answered correctly. |
| Drinking      | The whole group plus the space around them               | The questions are about seating and drinks set out, which a tight crop cuts off.                                             |
| Holdup        | Both people involved                                     | The questions are about how one person acts toward the other.                                                                |

*How the suggested status is worked out (no calculation)*

The suggested status is a lookup rule, not a score. It reads the AI's reply and moves the official status by at most one step, up or down. It is shown in the Status with AI context card and never applied.

| **AI reply (all with HIGH confidence)**                                                           | **Suggested status**                                                                                                               |
|---------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------|
| 'other_activity' (scene_type or hand_to_mouth_activity)                                           | one step LOWER: Likely → Possible, Possible → Monitoring; Monitoring stays Monitoring with the note 'AI: likely ordinary activity' |
| Drinking: drinking_likelihood 'likely' AND scene_type 'drinking_session'                          | one step HIGHER: Monitoring → Possible, Possible → Likely                                                                          |
| Smoking: hand_to_mouth_activity 'smoking'                                                         | one step HIGHER (puff-only: never above Possible)                                                                                  |
| Holdup: holdup_likelihood 'likely' AND (object_pointed_at_a_person OR scene_type 'confrontation') | one step HIGHER                                                                                                                    |
| official status already Likely and the AI supports it                                             | 'Likely — AI agrees'                                                                                                               |
| medium or low confidence, or any other answer                                                     | No change                                                                                                                          |
| no valid reply                                                                                    | AI context unavailable                                                                                                             |

Why going up is stricter: a wrong downward suggestion makes the tanod look more carefully, but a wrong upward suggestion can push an officer to act on someone who did nothing wrong. People also tend to trust an AI that says 'violation' (automation bias). So going up needs high confidence and, for drinking and holdup, two answers that agree. Notifications and priority always follow the official status, never the suggestion.

*Card wording*

| **Case**             | **Status with AI context card**                            |
|----------------------|------------------------------------------------------------|
| AI suggests higher   | Possible → Likely (suggested) — AI sees a drinking session |
| AI agrees at the top | Likely — AI agrees                                         |
| AI suggests lower    | Likely → Possible (suggested) — AI sees ordinary activity  |
| No change            | No change — Possible                                       |

Why no AI points: in local tests the model misread small objects (a bottle as a cigarette) and once called a real drinking session 'other activity' with high confidence. Scoring on answers like these would make the status depend on the least reliable part of the system. Showing the AI's view beside the official status keeps its help — catching vendors and stores — while a wrong answer can never change what the tanod is told.

# 9. Questions sent to the AI checker

Each call sends the shared instructions plus the block for that violation. The backend fills {system_note} with what the detectors found, so the model explains the detection instead of searching for it. The observations field comes first so the model describes what it sees before it judges. True/false fields are about objects and are answered true only if visible; likelihood and choice fields are the model's best judgment from what is visible.

*Shared instructions (sent with every call)*

You are reviewing a short CCTV video from a barangay street camera in the Philippines.  
The video is cropped around the person or group of interest. The frames are in time order.  
System detections (from the object and pose models): {system_note}  
Describe what you see first, then answer.  
For true/false fields about objects, answer true only if you can see them.  
For likelihood and choice fields, give your best judgment from what is visible.  
Pick "unclear" if you cannot tell.  
Rate confidence: "high" if the evidence is clearly visible, "medium" if it is  
partly visible or you are partly sure, "low" if you are mostly guessing.  
Reply only with the JSON below. No other text.

*Examples of {system_note}*

| **Violation**       | **system_note**                                              |
|---------------------|--------------------------------------------------------------|
| Drinking            | bottle detected; 3 people stationary together for 12 minutes |
| Smoking             | smoking item detected; hand reached the mouth 2 times        |
| Smoking (puff-only) | hand reached the mouth 3 times; no smoking item detected     |
| Holdup              | knife detected; two people standing still close together     |

*Drinking*

{  
"observations": "one sentence, under 20 words, only what you see",  
"drinking_likelihood": "likely" \| "possible" \| "unlikely",  
"table_chairs_or_seating_visible": true/false,  
"drinking_items_visible": true/false,  
"scene_type": "drinking_session" \| "other_activity" \| "unclear",  
"confidence": "high" \| "medium" \| "low"  
}  
Definitions:  
- observations: the people, objects and actions you can see. No conclusions.  
- drinking_likelihood: how likely it is that people are drinking at this spot.  
"likely" = someone drinks from a bottle, can or cup, or drinks are shared;  
"possible" = people are gathered with drinks nearby, but no drinking is seen;  
"unlikely" = no sign of drinking.  
- table_chairs_or_seating_visible: chairs, stools, benches, or a table used by the people.  
- drinking_items_visible: drinks, glasses, cups, plates or snacks set down near the people.  
- scene_type: "drinking_session" if people are drinking or gathered with drinks;  
"other_activity" ONLY if you can clearly see an ordinary activity, such as a store  
selling drinks, someone carrying or delivering drinks, or people eating a meal.  
Vehicles or people passing in the background do not count;  
"unclear" if you cannot tell.

*Smoking*

{  
"observations": "one sentence, under 20 words, only what you see",  
"smoking_item_visible": true/false,  
"hand_to_mouth_activity": "smoking" \| "other_activity" \| "none" \| "unclear",  
"confidence": "high" \| "medium" \| "low"  
}  
Definitions:  
- observations: the person's hands, what they hold, and what they do. If smoke or vapor  
is clearly leaving the person's mouth, nose, or a held item, mention it; otherwise do  
not mention smoke. No conclusions.  
- smoking_item_visible: a small thin object (cigarette) or a small device (vape)  
held between the fingers or at the lips.  
- hand_to_mouth_activity: what the hand is doing when it is at the mouth.  
"smoking" = fingers held together at the lips briefly, then the hand moves away;  
may hold a small thin object or exhale smoke;  
"other_activity" = an ordinary reason, such as eating, drinking from a bottle or cup,  
using a phone, or wiping the face;  
"none" = the hand never reaches the mouth;  
"unclear" = you cannot tell.

*Holdup*

{  
"observations": "one sentence, under 20 words, only what you see",  
"object_pointed_at_a_person": true/false,  
"victim_response_visible": true/false,  
"holdup_likelihood": "likely" \| "possible" \| "unlikely",  
"scene_type": "confrontation" \| "other_activity" \| "unclear",  
"confidence": "high" \| "medium" \| "low"  
}  
Definitions:  
- observations: the people, the object, and how they act toward each other. No conclusions.  
- object_pointed_at_a_person: a knife or sharp object is pointed or held toward another person.  
- victim_response_visible: a person raising their hands, handing over items, or backing away.  
- holdup_likelihood: how likely it is that one person is robbing or threatening another.  
"likely" = threatening behaviour is visible; "possible" = tense or unusual, but no clear threat;  
"unlikely" = no sign of a threat.  
- scene_type: "confrontation" if one person appears to be threatening another;  
"other_activity" ONLY if you can clearly see ordinary use of the object, such as vending,  
food preparation, work, or play; "unclear" if you cannot tell.

*What each field does*

| **Field**                                                                 | **Answer**                                 | **Used for**                                                                |
|---------------------------------------------------------------------------|--------------------------------------------|-----------------------------------------------------------------------------|
| observations                                                              | one sentence                               | shown in the alert details (cut to 20 words), marked AI-generated           |
| drinking_likelihood / holdup_likelihood                                   | likely / possible / unlikely               | AI badge, checklist; with scene_type, the upward suggestion                 |
| table_chairs_or_seating_visible, drinking_items_visible                   | true/false                                 | checklist                                                                   |
| smoking_item_visible, object_pointed_at_a_person, victim_response_visible | true/false                                 | checklist                                                                   |
| hand_to_mouth_activity                                                    | smoking / other_activity / none / unclear  | AI badge; suggested status (Section 8)                                      |
| scene_type                                                                | violation scene / other_activity / unclear | AI badge; suggested status (Section 8)                                      |
| confidence                                                                | high / medium / low                        | only 'high' moves the suggested status, up or down; shown next to the badge |

*Removed questions and why*

| **Removed field**                                             | **Why**                                                                       |
|---------------------------------------------------------------|-------------------------------------------------------------------------------|
| beverage_container_visible, knife_or_sharp_object_visible     | repeat the YOLO gate                                                          |
| smoking_item_at_hand_or_lips, hand_raised_to_mouth            | repeat the pose indicators                                                    |
| drinking_glass_or_cup_visible, food_or_snacks_visible         | too small to identify; merged into drinking_items_visible                     |
| group_appears_to_be_drinking_together, appears_to_be_a_holdup | true/false was too strict for a judgment; replaced by likelihood fields       |
| person_appears_to_be_smoking                                  | merged into hand_to_mouth_activity                                            |
| reason                                                        | renamed observations and moved first, so the model describes before it judges |
| drinking / eating / phone options                             | merged into other_activity                                                    |

# 10. Parking Obstruction

No points, no indicators and no AI checker. A vehicle at least halfway past the road edge for five minutes raises an alert. Ordinance No. 601 defines the rule exactly, so a score would add nothing.

# 11. Worked examples

*Drinking — a real inuman*

| **Time** | **What happens**                              | **Score** | **Status**            | **With AI context** |
|----------|-----------------------------------------------|-----------|-----------------------|---------------------|
| 19:44    | 3 people stop near a store                    | 10        | not shown (no bottle) | —                   |
| 19:53    | still there 10 minutes, evening               | 30        | not shown             | —                   |
| 19:55    | bottle seen for 2 seconds (AI checker called) | 70        | Possible              | —                   |
| 19:56    | bottle at the mouth                           | 85        | Likely                | —                   |
| 19:56    | AI: 'likely', seating, 'drinking_session'     | 85        | Likely                | Likely — AI agrees  |

*Behaviour points build up silently; when the bottle appears the score appears at once (30 + 40 = 70), so this event skips Monitoring and opens at Possible.*

*Drinking — the AI is wrong*

| **Time** | **What happens**                                    | **Score** | **Status** | **With AI context**  |
|----------|-----------------------------------------------------|-----------|------------|----------------------|
| 20:10    | group + bottle + bottle at mouth + 10 min + evening | 85        | Likely     | —                    |
| 20:10    | AI: 'other_activity', high confidence               | 85        | Likely     | Possible (suggested) |

*This happened in a local test. The official status stays Likely; the tanod sees the AI's doubt beside it.*

*Drinking — someone carrying a bottle*

| **Time** | **What happens**                        | **Score** | **Status** | **With AI context** |
|----------|-----------------------------------------|-----------|------------|---------------------|
| 15:10    | person walks past with a bottle for 2 s | 40        | Monitoring | —                   |
| 15:11    | keeps walking, no group                 | 40        | drops off  | —                   |

*Monitoring makes the system proactive, but it also lists harmless passers-by. That is why Monitoring is a quiet watchlist with no notification.*

*Smoking — walking smoker, cigarette detected*

| **Time** | **What happens**                              | **Score** | **Status** | **With AI context**  |
|----------|-----------------------------------------------|-----------|------------|----------------------|
| 16:20    | smoking item seen for 2 s (AI checker called) | 40        | Monitoring | —                    |
| 16:20    | AI: 'smoking', high confidence                | 40        | Monitoring | Possible (suggested) |
| 16:20    | one puff                                      | 60        | Possible   | —                    |
| 16:20    | item at the mouth                             | 75        | Likely     | —                    |
| 16:20    | (same AI answer)                              | 75        | Likely     | Likely — AI agrees   |

*The suggestion is worked out against the current official status, so it updates as the status rises.*

*Smoking — walking smoker, cigarette missed*

| **Time** | **What happens**                          | **Score** | **Status** | **With AI context** |
|----------|-------------------------------------------|-----------|------------|---------------------|
| 16:40    | one puff, no item → high-resolution check | 20        | not shown  | —                   |
| 16:40    | check finds the cigarette (2 s)           | 60        | Possible   | —                   |

*If the check finds nothing, the puff is logged only.*

*Smoking — lingering, cigarette never detected*

| **Time** | **What happens**                                | **Score** | **Status** | **With AI context**    |
|----------|-------------------------------------------------|-----------|------------|------------------------|
| 14:03    | hand to mouth                                   | 20        | not shown  | —                      |
| 14:04    | second puff                                     | 40        | not shown  | —                      |
| 14:06    | third puff within 5 minutes (AI checker called) | 55        | Possible   | —                      |
| 14:06    | AI: 'other_activity' (eating), high confidence  | 55        | Possible   | Monitoring (suggested) |

*Puff-only can never reach Likely, not even as a suggestion: had the AI answered 'smoking', the card would show 'No change — Possible'. It always carries the tag 'based on hand movement only'.*

*Holdup — a vendor (3–6 PM, x1.36 applied to every row)*

| **Time** | **What happens**                                  | **Score** | **Status** | **With AI context**  |
|----------|---------------------------------------------------|-----------|------------|----------------------|
| 16:30    | knife near a wrist (AI checker called): 45 x 1.36 | 61        | Possible   | —                    |
| 16:30    | two people frozen together: 65 x 1.36             | 88        | Likely     | —                    |
| 16:30    | AI: 'other_activity' (vending), high confidence   | 88        | Likely     | Possible (suggested) |

*The vendor still reaches the tanod, with the AI's note that it looks like vending.*

*Holdup — a real one at night (9 PM–12 AM, x0.87 applied to every row)*

| **Time** | **What happens**                                                         | **Score** | **Status**           | **With AI context** |
|----------|--------------------------------------------------------------------------|-----------|----------------------|---------------------|
| 22:15    | person loitering first: 10 x 0.87                                        | 9         | not shown (no knife) | —                   |
| 22:16    | second person, both freeze: 30 x 0.87                                    | 26        | not shown            | —                   |
| 22:16    | knife seen for 2 s (AI checker called): 75 x 0.87                        | 65        | Possible             | —                   |
| 22:16    | AI: pointed, victim reacting, 'likely', 'confrontation', high confidence | 65        | Possible             | Likely (suggested)  |

*The official status stays Possible (still notified); the card suggests Likely, so the tanod sees that both the detectors and the AI point to a holdup.*

# 12. Answering the panel

*"Ano basis niyo sa Monitoring, Possible and Likely?"*

"The status comes only from measurable indicators: the object detector, the pose model, the tracker and the clock. When the object is detected for about two seconds the event is listed as Monitoring, so officers can watch early. Each indicator then adds points ranked from the studies we cited — the object carries the most, supporting behaviour less. Possible starts at 55 and Likely at 75; we chose those cut-offs by testing several on our own recorded clips and comparing precision and recall. The AI checker does no scoring. It shows context and a suggested status beside the official one, and the tanod decides."

| **Likely question**             | **Short answer**                                                                                                                                                                                                                                          |
|---------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Why 40 for a bottle and not 50? | Ranked by importance from the studies, then checked on our clips. The order matters more than the exact number: object \> behaviour \> time.                                                                                                              |
| Is 75 a 75% chance?             | No. It is a bounded evidence score, not a probability.                                                                                                                                                                                                    |
| Why show Monitoring at all?     | Proactiveness: officers see a possible situation as soon as the object appears, before enough evidence builds up. It is a quiet watchlist, so it does not send notifications.                                                                             |
| Does the AI change the status?  | No. It does no calculation. It shows a suggested status in a separate card: one step lower if it is confident the scene is ordinary activity, one step higher if it is confident the scene is a violation. Notifications follow the official status only. |
| Why doesn't the AI add points?  | In our tests it misread small objects and once misjudged a drinking session. Scoring on it would make the status depend on the least reliable part of the system.                                                                                         |
| Then why use the AI at all?     | It gives the tanod context the detectors cannot, and flags common false alarms like vendors with knives.                                                                                                                                                  |
| Who makes the final decision?   | The tanod. The system proposes; the officer confirms.                                                                                                                                                                                                     |

*What must be backed by data before the defense*

- **Threshold sweep.** Labelled clips (real violation / not), run through the system; report precision and recall at several cut-offs. This is the basis for 55 and 75.

- **Honest fallback.** If full calibration is not done in time, say so: the weights are an initial ranking from the literature, the thresholds were checked on test clips, and full calibration is future work.

- **AI suggestion accuracy.** Log every suggestion in both directions and compare with the tanod's decision: a downward suggestion is right when the event is Dismissed, an upward one when it is Verified. If upward suggestions are often wrong, turn them off.

- **Model choice.** Same clips through 2B video and 4B frames; record answers, time and GPU memory. Pick one model.

- **Citations pending.** 'Someone loitering first' and 'Repeated puff pattern' need open-access citations (2021–2026), or must be justified by the clip tests.

# 13. What to state plainly in the paper

- A weighted linear sum assumes the indicators are independent, which does not strictly hold. Dependent indicators are made conditional rather than additive.

- No published study provides weights for this indicator set. The values are ranked from the literature and checked on our own labelled clips.

- The Manila time probabilities come from a different city, combine robbery with theft, and cover only convicted cases.

- The statuses and thresholds are a design decision of this project, justified by our own threshold test.

- Monitoring lists every object detection that lasts about 2 seconds, including harmless ones such as a passer-by carrying a bottle. It is a watchlist, not an accusation.

- The visibility gate means a violation whose object is never detected produces no alert, except the capped puff-only smoking path. This is a deliberate precision-over-recall trade.

- The AI checker runs locally, so frames do not leave the device. It misidentifies small objects at CCTV resolution, cannot tell alcoholic from non-alcoholic drinks, and is less reliable at night. For these reasons it does no scoring and only shows context and a suggested status.

- The score is a bounded evidence score, not a probability.

# 14. Change history

*Version 6.1 → 6.2*

| **Change**                | **Version 6.1**                                                  | **Version 6.2**                                                                                                         |
|---------------------------|------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------|
| Status card               | status and the evidence checklist                                | the status only; the evidence checklist is behind a 'Details' toggle                                                   |
| Object confidence         | inside the Status card area                                      | its own card beside Status, shown as '78% conf' (object confidence × 100); '—' when no object was detected (puff-only) |
| AI context card           | badge, observations, answered fields and frames                  | badge and observations; the answered fields and 'Frames the AI saw' are behind 'Details'                               |
| ⓘ text                    | opened on hover                                                  | opens only when the ⓘ is clicked; closes on a click outside, Esc, or a second click                                    |
| Review tag                | Pending / Verified / Dismissed switch and badge                  | removed from the UI. The verdict is still recorded silently for evaluation: Dismiss (also after assignment, and an officer's false alarm) = false alarm; Assign officers = worth attending |
| Closed alerts             | a 'dismissal reason' box at the bottom                           | a Dismissed (soft red) or Resolved (soft green) banner under the header with who, when and why, plus a Timeline card; only 'Reopen' (admin) remains |
| Violations list cards     | status, review tag, '% conf'                                     | status; the assignment state (Unassigned / Assigned) on the right                                                      |
| Alert time (uploaded clips) | processing time                                                | 'Recorded at' + the event's position in the clip; 'processed at' when no recorded time was given. Test cameras show 'Uploaded footage' |

*Version 6 → 6.1*

| **Change**           | **Version 6**                          | **Version 6.1**                                                                                                  |
|----------------------|----------------------------------------|------------------------------------------------------------------------------------------------------------------|
| Where Monitoring shows | a watchlist panel on the Overview dashboard | quiet and without notification; viewed with the Include Monitoring filter on the Violations page. The Overview 'Recent Violations' lists Possible and Likely only |
| Wording              | 'active violation'                     | alerts are recorded incidents awaiting review: 'Pending Review', 'No incidents awaiting review', filter 'All'    |

*Version 5 → 6*

| **Change**           | **Version 5**                       | **Version 6**                                                                                                                                       |
|----------------------|-------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|
| AI-suggested status  | one step lower only                 | one step lower or higher; higher needs high confidence and, for drinking and holdup, two agreeing answers; puff-only never suggested above Possible |
| Card wording         | No change / Likely → Possible       | adds 'Possible → Likely (suggested)' and 'Likely — AI agrees'                                                                                       |
| Smoking observations | hands, what they hold, what they do | also mentions smoke or vapor only if clearly leaving the mouth, nose, or a held item                                                                |

*Version 4 → 5*

| **Change**            | **Version 4**                                                            | **Version 5**                                                                                                        |
|-----------------------|--------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------|
| Name                  | Level                                                                    | Status                                                                                                               |
| Statuses              | not shown under 55 / Possible / Likely                                   | Monitoring (object detected about 2 s, quiet watchlist) / Possible / Likely                                          |
| AI effect             | automatic brake: Likely → Possible on 'other_activity' + high confidence | no effect on the official status; shown as 'Status with AI context' (one step lower, display only)                   |
| AI checker trigger    | gate opens                                                               | event enters Monitoring (same moment)                                                                                |
| Puff-only             | AI brake could hide it                                                   | suggested status Monitoring; official stays Possible                                                                 |
| Alert UI              | Level + AI badge                                                         | Status card, AI context card, Status with AI context card; header tag becomes Review: Pending / Verified / Dismissed |
| 'Confidence 49%' card | —                                                                        | removed; replaced by the Status card                                                                                 |
