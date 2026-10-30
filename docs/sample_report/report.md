# Incident report — INC-2026-1012-01 — Red sedan / white SUV junction collision (demo)

Location: Main Rd x 5th Ave junction, Gachibowli, Hyderabad

Run `d605bfa9` · analysed 2026-10-30 18:39 UTC · reference time 2026-10-12 17:42:00 +0530 · report generated 2026-10-30 18:39 UTC

**Collision between V1 and V2 at t=10 s (17:42:10), supported by sensor, witness evidence; 2 high, 2 medium and 3 low-severity contradiction(s).**

> **AI-drafted summary** (from the facts below; verify against the cited evidence)
>
> At the time of the collision, sensor data and witness testimony confirm that V1, a red car, entered the junction from the south on a yellow signal, while V2, a big white SUV, entered from the west against a red signal. A high-severity contradiction exists regarding the northbound signal state: a witness claims the light was green when V1 entered, but sensor records show it turned yellow at t=7 s and remained yellow until t=10 s. Another high-severity contradiction involves V2’s speed: a witness estimated it at "at least 80 km/h," but telemetry records show a consistent speed of 57.6 km/h. A medium-severity evidence gap arises from a telemetry dropout for V1 between t=6 s and t=7.5 s, during which the northbound signal turned yellow, making V1’s response to the signal unverifiable. Additionally, a witness claims V1 accelerated just before the collision, but telemetry shows V1 was braking from t=8.5 s onward, with speeds decreasing from 46.8 km/h to 36 km/h by t=10 s.

## Evidence

| File | Type | SHA-256 | Analysis |
|---|---|---|---|
| witness_01_pedestrian.txt | witness | `4f6453d2339a9323…` | analysed |
| witness_02_shopkeeper.txt | witness | `f2a981f5f925e660…` | analysed |
| signal_controller.csv | sensor | `cd3561c6e610af7e…` | analysed |
| v1_telematics.csv | sensor | `db986cadbcaf62da…` | analysed |
| v2_obd.csv | sensor | `c714336b13b32b8a…` | analysed |

## Timeline

| t | Clock | Event | Sources | Confidence |
|---|---|---|---|---|
| -20 s–11 s | 17:41:40 | Eastbound signal red | sensor | 100% |
| -20 s–7 s | 17:41:40 | Northbound signal green | sensor, witness | 100% |
| 0 s–9 s | 17:42:00 | V1 red car northbound at ~47 km/h | sensor, witness | 98% |
| 0 s–10 s | 17:42:00 | V2 big white SUV eastbound at ~58 km/h | sensor, witness | 98% |
| 6 s–7.5 s | 17:42:06 | V1 sensor data gap (6–7.5 s) | sensor | 100% |
| 7 s–10 s | 17:42:07 | Northbound signal yellow | sensor | 100% |
| 8.5 s–9 s | 17:42:08 | V1 begins braking | sensor, witness | 97% |
| 9.5 s–10 s | 17:42:09 | V2 begins braking | sensor | 97% |
| 10 s | 17:42:10 | Northbound signal red | sensor | 100% |
| 10 s | 17:42:10 | Signal yellow (approach not stated) | witness | 85% |
| 10 s | 17:42:10 | V1 accelerates (witness claim) | witness | 85% |
| 10 s | 17:42:10 | V1 enters the junction from the south on yellow | sensor, witness | 99% |
| 10 s | 17:42:10 | V2 enters the junction from the west against red | sensor | 95% |
| 10 s | 17:42:10 | Collision between V1 and V2 | sensor, witness | 100% |
| 11 s | 17:42:11 | Eastbound signal green | sensor | 100% |
| 11.5 s | 17:42:11 | V1 and V2 come to rest after the collision | sensor, witness | 100% |

## Contradictions

### 🔴 V2 speed overstated by W1 — high, numeric mismatch

W1 estimates the big white SUV at 'Then a big white SUV came from my left along 5th Ave really fast, I would say at least 80 km/h'; V2 telemetry records 57.6 km/h between t=0 s and t=10 s.

- `wit_w1_04` witness_01_pedestrian.txt @ line:10 — "Then a big white SUV came from my left along 5th Ave really fast, I would say at least 80 km/h"
- `sen_v2_001` v2_obd.csv @ row:2

### 🔴 Northbound signal state disputed — high, semantic

W1 says V1's light was green. The signal controller shows yellow from t=7 s to t=10 s. V1 entered the junction at t≈10 s.

- `wit_w1_03` witness_01_pedestrian.txt @ line:9 — "I am fairly sure its light was green when it was approaching"
- `sig_003` signal_controller.csv @ row:4

### 🟠 V1 telemetry dropout during yellow onset — medium, missing evidence

No V1 records between t=6 s and t=7.5 s, while the northbound signal changed to yellow at t=7 s. V1's reaction to the yellow cannot be confirmed from telemetry.

- `sen_v1_gap_01` v1_telematics.csv @ rows:14-15
- `sig_003` signal_controller.csv @ row:4

### 🟠 W2 claims V1 accelerated; telemetry shows braking — medium, semantic

W2 says the red car 'The light for Main Road had already turned yellow. The red car sped up to beat the light, I am sure of it, it was going fast'. V1 telemetry shows constant then decreasing speed (46.8 km/h at t=0 s, 46.8 km/h at t=9 s, 36 km/h at t=10 s), braking from t=9 s.

- `wit_w2_02` witness_02_shopkeeper.txt @ line:8 — "The light for Main Road had already turned yellow. The red car sped up to beat the light, I am sure of it, it was going fast"
- `sen_v1_001` v1_telematics.csv @ row:2
- `sen_v1_002` v1_telematics.csv @ row:18
- `sen_v1_003` v1_telematics.csv @ row:20

### ⚪ 'Eastbound signal red' has a single source — low, missing evidence

'Eastbound signal red' (t=-20 s–11 s) is supported only by the sensor/controller log. V2's entry on this signal rests on the sensor record alone; no camera or witness saw the signal head.

- `sig_001` signal_controller.csv @ row:2
- `sen_v2_003` v2_obd.csv @ row:22

### ⚪ 'Northbound signal yellow' has a single source — low, missing evidence

'Northbound signal yellow' (t=7 s–10 s) is supported only by the sensor/controller log. V1's entry on this signal rests on the sensor record alone; no camera or witness saw the signal head.

- `sig_003` signal_controller.csv @ row:4
- `sen_v1_003` v1_telematics.csv @ row:20

### ⚪ V2 vehicle type ambiguous — low, semantic

W2 is unsure whether V2 was an SUV or a van; W1 say SUV.

- `wit_w2_04` witness_02_shopkeeper.txt @ line:9 — "The white vehicle, I think it was an SUV or maybe a van, was coming from the west side"
- `wit_w1_04` witness_01_pedestrian.txt @ line:10 — "Then a big white SUV came from my left along 5th Ave really fast, I would say at least 80 km/h"

## Evidence gaps and limitations

- V1 telemetry dropout during yellow onset
- 'Eastbound signal red' has a single source
- 'Northbound signal yellow' has a single source
- *extraction.witness/featherless:Qwen/Qwen3-30B-A3B-Instruct-2507*: witness_01_pedestrian.txt line 10: direction 'eastbound' dropped (not stated in the quote).
- *extraction.witness/featherless:Qwen/Qwen3-30B-A3B-Instruct-2507*: witness_01_pedestrian.txt: LLM gave no relative order for the events.
- *extraction.sensor/pandas*: v1_telematics.csv: no records between t=6.0 s and t=7.5 s (2 sample(s) missing at 2 Hz).
- *extraction.sensor/pandas*: v2_obd.csv: speed converted from mph to km/h (raw value kept as speed_mph_raw).
- *extraction.sensor/pandas*: v2_obd.csv: unit_id OBD-7731 mapped to V2.
- *fusion*: Inferred: collision at t=10 s from sen_v1_004, sen_v2_004 (sudden heading changes of two vehicles within 1 s and 6 m).

## Reconstruction

Tracks for V1 · red car, V2 · big white SUV over t=5–13 s.
Collision point (-1.75 m, 1.75 m) at t=10 s, confidence 100%, supported by (derived) @ from:sen_v1_004,sen_v2_004, v1_telematics.csv @ row:21, v2_obd.csv @ row:23, witness_01_pedestrian.txt @ line:7, witness_02_shopkeeper.txt @ line:7, witness_01_pedestrian.txt @ line:12, witness_02_shopkeeper.txt @ line:11.

## Chain of custody

- 2026-10-30 18:39:29 UTC · evidence uploaded · witness_01_pedestrian.txt · sha256 `4f6453d2339a9323…`
- 2026-10-30 18:39:29 UTC · evidence uploaded · witness_02_shopkeeper.txt · sha256 `f2a981f5f925e660…`
- 2026-10-30 18:39:29 UTC · evidence uploaded · signal_controller.csv · sha256 `cd3561c6e610af7e…`
- 2026-10-30 18:39:29 UTC · evidence uploaded · v1_telematics.csv · sha256 `db986cadbcaf62da…`
- 2026-10-30 18:39:29 UTC · evidence uploaded · v2_obd.csv · sha256 `c714336b13b32b8a…`
- 2026-10-30 18:39:29 UTC · analysis started · d605bfa9
- 2026-10-30 18:39:43 UTC · analysis finished · d605bfa9

---
*Generated by the incident reconstruction pipeline. Times are seconds from the reference time; positions are metres from the junction centre (+x east, +y north).*