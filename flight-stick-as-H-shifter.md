# ATS H-Pattern Shifter via WinWing F16 Stick

## Concept

Use the WinWing F16 HOTAS stick's X/Y axes as a virtual H-pattern shifter for American Truck Simulator, with the Fanatec V3 clutch pedal functioning as a real clutch gate in H-pattern mode.

## Motivation

ATS's sequential transmission mode auto-manages the clutch, making the V3's clutch pedal essentially useless. Switching to H-pattern mode restores clutch gating behavior, but requires mapping individual gears to physical inputs. Rather than buying a dedicated H-pattern shifter, the F16 stick's X/Y throw can be divided into gear zones to simulate an H-gate.

## How It Will Work

- Set ATS transmission mode to **H-pattern**
- Clutch pedal will now gate all gear changes as desired
- A custom script reads the F16 stick's X/Y axes and maps positional zones to individual gear inputs that ATS recognizes as H-pattern gear selections
- Gear zones will be latched on entry (not continuous) to avoid spamming ATS with inputs
- Stick center = neutral (the spring return actually works in our favor here)
- Example zone layout for a 6-speed:

```
Left-Fwd  = 1st  |  Center-Fwd = 3rd  |  Right-Fwd = 5th
Left-Back = 2nd  |  Center-Back= 4th  |  Right-Back = 6th
                      Center   = Neutral
```

For trucks with more gears, range/splitter toggles can be mapped to buttons on the stick or wheel.

## Planned Tech Stack

- **vJoy** — virtual joystick device (already familiar with this from DCS rudder mapping)
- **Python** with `pygame` or `inputs` library to read stick axes
- Script maps X/Y axis zones to vJoy virtual button presses that ATS reads as H-pattern gear inputs

## Current Blocker

The F16 stick needs to be **physically mounted to the sim rig frame** before software development can begin — stable, consistent stick position is essential for reliable zone detection and testing. A cardboard box doesn't cut it.

## Phase 1 — Hardware (Do First)

- [ ] Design and build a mounting adapter for the WinWing F16 stick to attach to the sim rig frame
- [ ] Ensure mount positions the stick comfortably for shifting motion without interfering with wheel/pedal use

## Phase 2 — Software (After Mount is Built)

- [ ] Write axis zone mapping script in Python
- [ ] Implement gear latch logic (fire input once on zone entry)
- [ ] Tune zone boundaries and dead zones for feel
- [ ] Test extensively in ATS with clutch pedal gating
- [ ] Tweak zone layout for the specific truck transmissions used in-game

## Notes

- This approach costs nothing beyond time — all required hardware is already owned
- The physical resistance and center-return spring of the F16 stick gives a natural "finding the gate" feel that may translate well to shifting
- May need to experiment with zone sizing — the stick's throw range vs. a real H-shifter's gate spacing will need calibration
