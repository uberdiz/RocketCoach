# Match 1 report

> **SIMULATED DATA: not from a real Rocket League match.**

You lost 13-15. You were at the coach's recommended spot 46% of the time. Biggest issue: 50/50 lost (26x). Boost under 20 for 80% of play; goal-side 54% of the time.

## Highest-impact mistakes
1. **50/50 lost** (challenge, 26x, confidence 0.65)
   Why: you went in with 12 boost.
   **Next time:** Arrive with some boost (grab a small pad on the way).
   At: 0:06, 0:21, 0:43, 1:34, 2:05, 2:08, 2:23, 3:05. Evidence: `i_touched=True, boost=12, speed=1463, approach=1.0`
2. **Last back: shadow, don't dive** (defense, 9x, confidence 0.74)
   You challenged as last back while the opponent reached the ball 0.4s before you and your teammate was upfield. Retreat goal-side and challenge when they touch or your teammate rotates.
   **Next time:** When you're last back and they'll touch first, back off goal-side and challenge after their touch.
   At: 0:18, 0:50, 0:55, 1:07, 1:12, 1:17, 2:09, 4:58. Evidence: `ttb_me=0.5, ttb_opp=0.15, ball_dist_goal=5755`
3. **Net open: get goal-side now** (defense, 4x, confidence 0.84)
   The opponents control the ball in your half and neither of you is between the ball and the net. Drive straight back to the far post before turning to face the play.
   **Next time:** The moment they win the ball in your half, go straight to your back post, then turn to face it.
   At: 0:56, 1:08, 1:20, 6:54. Evidence: `ball_dist_goal=919, threat=1.0`

## Strengths
- Not enough evidence yet.

## Grades
| Area | Grade | Samples | Confidence | Criteria |
|---|---|---|---|---|
| Positioning & defense | F | 304s of play | normal | Start 100; -10 per last-back over-commit per 5 min; -1.2 per % of time with the net exposed; -0.5 per % goal-side time below 55%. |
| Rotation & decisions | A | 304s of play | normal | Start 100; -12 per double commit per 5 min; -0.4 per % of time nobody covered the net. |
| Boost management | F | 304s of play | normal | Start 100; -1.2 per % of time under 20 boost beyond 25%; -1 per 5 boost burned while supersonic; -1 per 3s starved and out of the play. |
| Aerials | n/a | 1 attempts | insufficient data | 30 + 70 x contact rate (contact = touch while airborne after a takeoff toward a ball above 250uu). |
| Recoveries | n/a | 1 landings | insufficient data | 100 - 60 per second of average time-to-1200uu/s after landing beyond 0.8s. |
| Kickoffs (estimate) | D | 29 decided | normal | Share of decided kickoffs where the ball ended in the opponents' half after 2s. |

## Boost
- Under 20 / 40 / 60: 80.2% / 93.4% / 98.2%
- Collected 1940, spent 1972 (supersonic waste 129, in air 43, while defending 41)
- Pads: 4 big, 91 small; 127 small pads passed close by while under 50
- Corner-100 trips: 3 (1 while last back)
- Low boost but well positioned: 68.8s (fine); starved and out of play: 8.5s (wasteful)
- Average boost entering 50/50s: 10.2 (37 under 20)

## Mechanics (measured unless marked estimate)
- Touches 58; shots on target (estimate) 4, avg ball speed 1916; corner placements 0
- Aerials: 0/1 contact; double-jump takeoffs 0; takeoff distance hit n/a vs miss 218.0
- Recoveries: 1 landings, avg 2.52s to 1200uu/s
- Kickoffs (estimate): won 13, lost 16 of 29
- 50/50s (estimate): won 22, lost 26 of 48
- Dribbles 0 (flicks 0); wall touches 0; backboard touches 0
- Not measured: air_roll (Controller roll input alone doesn't show skill; would need orientation-goal analysis.); wavedash_halfflip (Needs reliable dodge-direction + landing analysis; not yet validated on real data.); wall_read_quality (Needs replay/video review of approach paths; only wall touch counts are logged.); flip_reset (Detectable from jump state but not validated; ignored for grading.)

## Compared with previous matches
- Last 5: no previous matches yet.
- Last 20: no previous matches yet.

## Next session training plan
*5 min warm-up, ~8 min per focus pack (stop when the success target is reached), then play an offline match concentrating only on the first focus skill.*

1. **Warm-up (5 min Free Play)**: Hit the ball around the field at speed, two powershots, two aerials, one kickoff from each spawn. No scores, just get your timing in.
2. **Shadow Defense**: because 13 last-back over-commit / open-net situations.
   Pack: **Shadow Defense 101** `6726-51F1-6B0D-9540` by Lazord (Champion), verified, source https://prejump.com/training-packs/6726-51F1-6B0D-9540
   Success: Stay goal-side and make the save/clear on 7 of 10 shots in two runs in a row. When you hit the success target in two separate sessions, move to a harder pack for the same skill.
   If the code fails: In game: Training > Custom Training > Search, type 'Shadow Defense 101'.
   Drill: **Shadow-defense habit (in matches)**: When an opponent dribbles at you and you're last back, drive backwards at their pace, stay goal-side, and only challenge after their touch or when your teammate is behind you. Success: No 'Last back' or 'Net open' tips in a full match.
3. **Kickoffs**: because kickoffs won 13 / lost 16 (estimate).
   Pack: **Speed Flip** `936E-C293-5DF5-2D5C` by Shazanwich (Diamond), verified, source https://prejump.com/training-packs/936E-C293-5DF5-2D5C
   Success: Do 10 speed-flip kickoffs in a row reaching the ball under 2.3s (use the game clock). When you hit the success target in two separate sessions, move to a harder pack for the same skill.
   If the code fails: In game: Training > Custom Training > Search, type 'Speed Flip'.
   Drill: **Kickoff reps (Free Play)**: Reset with the kickoff key and practise your kickoff from each spawn, 5 reps each. Success: Reach the ball first-flip on 4 of 5 from every spawn.

## Play reviews: what to do differently
- `0:06` **50/50 lost**: Why: you went in with 12 boost. **Next time:** Arrive with some boost (grab a small pad on the way).
- `0:08` **Goal against: you were out of position**: Before the shot your job was getting into the net, but you were ahead of the ball (in position 20% of that time). **Next time:** When they win the ball in your half, drive straight to the back post first, then turn and face it.
- `0:21` **Goal against: you were out of position**: Before the shot your job was shadowing goal-side, but you were ahead of the ball (in position 27% of that time). **Next time:** When they win the ball in your half, drive straight to the back post first, then turn and face it.
- `0:21` **50/50 lost**: Why: you went in with 6 boost; you hit it from the side/backwards, not toward their half; you were last back with no cover. **Next time:** Arrive with some boost (grab a small pad on the way).
- `0:43` **50/50 lost**: Why: you went in with 12 boost. **Next time:** Arrive with some boost (grab a small pad on the way).
- `0:57` **Goal against: you were out of position**: Before the shot your job was shadowing goal-side, but you were ahead of the ball (in position 0% of that time). **Next time:** When they win the ball in your half, drive straight to the back post first, then turn and face it.
- `1:14` **50/50 won, but it was risky (last back)**: Good contact. Risk: you were last back with no cover. **Next time:** As last back only challenge when you clearly get there first; otherwise shadow.
- `1:21` **Goal against: you were out of position**: Before the shot your job was shadowing goal-side, but you were ahead of the ball (in position 0% of that time). **Next time:** When they win the ball in your half, drive straight to the back post first, then turn and face it.
- `1:34` **Goal against: you were out of position**: Before the shot your job was shadowing goal-side, but you were ahead of the ball (in position 29% of that time). **Next time:** When they win the ball in your half, drive straight to the back post first, then turn and face it.
- `1:34` **50/50 lost**: Why: you went in with 0 boost; you hit it from the side/backwards, not toward their half; you were last back with no cover. **Next time:** Arrive with some boost (grab a small pad on the way).
- `1:45` **Goal against: you were out of position**: Before the shot your job was getting into the net, but you were 2915uu off the spot (in position 20% of that time). **Next time:** When they win the ball in your half, drive straight to the back post first, then turn and face it.
- `1:56` **Goal against: you were out of position**: Before the shot your job was getting into the net, but you were ahead of the ball (in position 0% of that time). **Next time:** When they win the ball in your half, drive straight to the back post first, then turn and face it.
- `2:05` **50/50 lost**: Why: you went in with 0 boost. **Next time:** Arrive with some boost (grab a small pad on the way).
- `2:08` **50/50 lost**: Why: you went in with 0 boost; you hit it from the side/backwards, not toward their half. **Next time:** Arrive with some boost (grab a small pad on the way).
- `2:23` **50/50 lost**: Why: you went in with 6 boost; you hit it from the side/backwards, not toward their half. **Next time:** Arrive with some boost (grab a small pad on the way).
- `2:27` **Goal against: you were out of position**: Before the shot your job was supporting behind the play, but you were ahead of the ball (in position 0% of that time). **Next time:** When they win the ball in your half, drive straight to the back post first, then turn and face it.
- `3:05` **50/50 lost**: Why: you went in with 12 boost. **Next time:** Arrive with some boost (grab a small pad on the way).
- `3:08` **50/50 lost**: Why: you went in with 12 boost; you hit it from the side/backwards, not toward their half. **Next time:** Arrive with some boost (grab a small pad on the way).
- `3:17` **50/50 lost**: Why: you hit it from the side/backwards, not toward their half. **Next time:** Take a wider line so your nose points upfield at contact.
- `3:40` **Goal against: you were out of position**: Before the shot your job was covering behind the 50/50, but you were ahead of the ball (in position 27% of that time). **Next time:** When they win the ball in your half, drive straight to the back post first, then turn and face it.
- `3:49` **50/50 lost**: Why: you went in with 0 boost. **Next time:** Arrive with some boost (grab a small pad on the way).
- `3:52` **50/50 won, but it was risky (last back)**: Good contact. Risk: you were last back with no cover. **Next time:** As last back only challenge when you clearly get there first; otherwise shadow.
- `3:55` **50/50 lost**: Why: you went in with 12 boost. **Next time:** Arrive with some boost (grab a small pad on the way).
- `3:59` **50/50 lost**: Why: you went in with 0 boost. **Next time:** Arrive with some boost (grab a small pad on the way).
- `4:17` **50/50 won, but it was risky (last back)**: Good contact. Risk: you were last back with no cover. **Next time:** As last back only challenge when you clearly get there first; otherwise shadow.
- `4:29` **50/50 lost**: Why: you went in with 12 boost. **Next time:** Arrive with some boost (grab a small pad on the way).
- `4:33` **Goal against: you were out of position**: Before the shot your job was supporting behind the play, but you were ahead of the ball (in position 0% of that time). **Next time:** When they win the ball in your half, drive straight to the back post first, then turn and face it.
- `4:45` **50/50 lost**: Why: you went in with 0 boost. **Next time:** Arrive with some boost (grab a small pad on the way).
- `4:51` **50/50 lost**: Why: you went in with 0 boost. **Next time:** Arrive with some boost (grab a small pad on the way).
- `4:53` **50/50 lost**: Why: you went in with 0 boost; you hit it from the side/backwards, not toward their half. **Next time:** Arrive with some boost (grab a small pad on the way).

## Timeline
- `0:06` [challenge] 50/50 lost (shown live)
- `0:07` [kickoff] Kickoff lost (estimate)
- `0:08` [goal] Goal against
- `0:08` [defense] Goal against: you were out of position (shown live)
- `0:18` [defense] Last back: shadow, don't dive
- `0:18` [kickoff] Kickoff lost (estimate)
- `0:21` [goal] Goal against
- `0:21` [defense] Goal against: you were out of position (shown live)
- `0:31` [kickoff] Kickoff won (estimate)
- `0:34` [goal] Goal for your team
- `0:34` [offense] Goal! Nice finish (shown live)
- `0:43` [challenge] 50/50 lost (shown live)
- `0:44` [kickoff] Kickoff lost (estimate)
- `0:50` [defense] Last back: shadow, don't dive (shown live)
- `0:55` [defense] Last back: shadow, don't dive
- `0:56` [defense] Net open: get goal-side now
- `0:57` [goal] Goal against
- `0:57` [defense] Goal against: you were out of position (shown live)
- `1:07` [kickoff] Kickoff lost (estimate)
- `1:07` [defense] Last back: shadow, don't dive
- `1:08` [defense] Net open: get goal-side now
- `1:12` [defense] Last back: shadow, don't dive
- `1:14` [challenge] 50/50 won, but it was risky (last back) (shown live)
- `1:17` [defense] Last back: shadow, don't dive
- `1:20` [defense] Net open: get goal-side now (shown live)
- `1:21` [goal] Goal against
- `1:21` [defense] Goal against: you were out of position (shown live)
- `1:31` [kickoff] Kickoff lost (estimate)
- `1:34` [goal] Goal against
- `1:34` [defense] Goal against: you were out of position (shown live)
- `1:44` [kickoff] Kickoff lost (estimate)
- `1:45` [goal] Goal against
- `1:45` [defense] Goal against: you were out of position (shown live)
- `1:55` [kickoff] Kickoff lost (estimate)
- `1:56` [goal] Goal against
- `1:56` [defense] Goal against: you were out of position (shown live)
- `2:06` [kickoff] Kickoff lost (estimate)
- `2:08` [challenge] 50/50 lost (shown live)
- `2:09` [defense] Last back: shadow, don't dive (shown live)
- `2:16` [challenge] 50/50 won: ball went upfield (shown live)
- `2:23` [challenge] 50/50 lost (shown live)
- `2:27` [goal] Goal against
- `2:27` [defense] Goal against: you were out of position (shown live)
- `2:37` [kickoff] Kickoff won (estimate)
- `2:38` [goal] Goal for your team
- `2:47` [kickoff] Kickoff won (estimate)
- `2:48` [goal] Goal for your team
- `2:48` [offense] Goal: good support (shown live)
- `2:58` [kickoff] Kickoff won (estimate)
- `3:05` [challenge] 50/50 lost (shown live)
- `3:13` [challenge] 50/50 won: ball went upfield (shown live)
- `3:28` [rotation] Teammate's ball: peel off (shown live)
- `3:40` [goal] Goal against
- `3:40` [defense] Goal against: you were out of position (shown live)
- `3:50` [kickoff] Kickoff lost (estimate)
- `3:52` [challenge] 50/50 won, but it was risky (last back) (shown live)
- `3:59` [challenge] 50/50 lost (shown live)
- `4:07` [goal] Goal for your team
- `4:07` [offense] Goal! Nice finish (shown live)
- `4:17` [challenge] 50/50 won, but it was risky (last back) (shown live)

## Data
Source: mock (simulated=True, complete=True, gaps=0). Capabilities: controller_inputs, ball_prediction, boost_pad_states, latest_touch, jump_dodge_state.
Live tips shown: 50 of 95 observations; suppressed: {'low_confidence': 4, 'busy': 25, 'rule_cooldown': 11, 'duplicate': 5}
