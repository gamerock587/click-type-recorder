# Click & Type Recorder

Double-click **Start Recorder.bat**. It installs the two small Python dependencies if needed.

1. Put one account name per line in **accounts.txt**, or use Browse to choose another text file. Empty lines are ignored.
2. Open the target app and put it in the position you will use for playback.
3. Copy a username before recording. Press **F6**. Recording starts **immediately**, including your taskbar click to switch to the target app. A floating **RECORDING NOW** indicator shows how many clicks were captured. Your clipboard is left as you copied it. The countdown setting applies only to playback.
4. Click the name field and paste normally with **Ctrl+V**. Type the password normally; the same password repeats each loop. Alternatively, **F8** inserts an explicit account step. Use Ctrl+A first if the field already contains text.
5. Finish the sequence, including the steps needed to return the app to the same starting screen for the next account. Press **F6** again to finish recording. Your recording is saved automatically.
6. Select **Account list** and press **F7** / Run. You will always be asked which account number to start with. Enter **5** to include the fifth account; enter **6** if you already finished the first five. It runs through the last account, then stops.

For a fixed number of repetitions, record a sequence without F8, select **Repeat recording**, enter the number, and press Run.

**F9 stops recording, playback, or the countdown at any time.** The app releases any keys or mouse buttons it held during playback.

At the beginning of every account loop, the current name is also copied to the clipboard. You can record Ctrl+V instead of F8 if you prefer. F8 explicitly copies the current account again at its insertion point, even if another step changed the clipboard.

## Wait for a color — F2

While recording, move the mouse over the screen color you want to wait for and press **F2**. The desired color must be visible when you capture it. Then continue recording your next click or typing. You can add several color checks in one recording.

During playback, the recorder returns the mouse to that saved screen position and checks its color immediately. If the exact RGB color does not match, it checks again **every 2 seconds**. No later click or typing runs until the color matches. It waits as long as needed; **F9** stops the wait immediately and leaves that account available to retry. Recording itself continues normally after F2; the repeated checks happen during playback.

The window shows the saved color, coordinates, and current color while waiting. Choose a stable, solid-colored part of the target, and keep that area visible. Positions are absolute screen coordinates, so moving the target window changes what the recorder sees.

## Three monitors and screenshots — F3

New recordings save the Windows monitor IDs and monitor bounds. Each mouse action and color check remembers its own monitor. Put the mouse on your target screen when pressing F6. If you move between screens while recording, actions on all those monitors are kept.

Playback uses the saved display and the same position within that display, including screens to the left of or above the primary monitor. If Windows rearranges the displays, coordinates are adjusted using the saved monitor IDs. If a required monitor is disconnected or its resolution changed, playback stops before sending input. Keep the target app at the same position within its monitor. Older recordings without monitor metadata retain their absolute positions; record a new sequence to save monitor information.

Press **F3** to capture the **entire monitor under your mouse**, including its taskbar, and send that single screen to the configured Telegram chat. It does not combine the three screens. If F3 is pressed while recording, it sends a screenshot immediately and saves a screenshot step. Every replay sends a fresh screenshot of that saved monitor at that point in the sequence, even when the mouse is on another screen.

The Telegram row displays delivery success or an error. A failed screenshot step stops playback and does not mark the account complete. F9 stops playback immediately while an upload is pending; an upload already sent to Telegram may still arrive. Wait for a manual F3 upload to finish before pressing F3 again.

Telegram is configured using the supplied bot and chat. Use **Telegram…** to change these settings. The token is stored in `.telegram_credentials`, protected by Windows DPAPI for your Windows account; it is not included in recording files. Screenshots are held in memory and uploaded over HTTPS. No screenshot files are saved automatically.

## Telegram live status

Each playback run sends one status message such as **Starting in account :: name3** with **Progress: 0%**. The recorder edits that same message as the current account and percentage change, at most about once every two seconds. It shows both the overall percentage for this selected run and progress through the current account's recorded steps. Progress is based on completed recorded steps, not elapsed time or website verification; it pauses while waiting for a color.

Finishing an account or the entire run only updates the status message; it does not capture or send a screenshot. Screenshots are sent only by pressing **F3** or replaying a recorded F3 step. The final status becomes **Done — 100%**. If you stop or an action fails, the same message changes to **Stopped** or **Failed**.

Status edits run in the background, so input playback does not wait for Telegram progress updates. Delivery errors appear in the Telegram status row. When you close the app, it finishes pending Telegram updates before exiting.

The repaired `last_recording.json` starts by opening `C:\Program Files (x86)\7Road\wan\wartune\client2.exe` (or activating its existing window), waits three seconds, then replays the original sequence. This prefix runs at the beginning of each loop. A copy of the original recording is in `recording_backups`.

If playback seems stuck, look at the floating status indicator. A color wait shows both the expected color and the current color and blocks later clicks until they match. Start playback on the same initial app screen as the recording. The indicator stays on an unused monitor where possible and does not take keyboard focus or add a taskbar icon.

The app remembers the next account after each fully replayed loop and suggests it next time. An interrupted loop is not marked complete. Completion means that the recorded actions finished; the recorder does not check whether the website accepted the account.

Mouse positions and pauses are replayed as recorded. Keep the same window size, keyboard layout, and Caps Lock state. Allow enough time for pages to load while recording, and adjust the delay between loops if needed. F2, F3, and F6–F9 are reserved while this app is open. Avoid manually using the mouse or typing during playback except to stop it.

Mouse playback uses checked Windows input across the entire desktop and verifies the destination before clicking. A missed movement first receives three normal attempts. If these fail, or Windows temporarily rejects movement, playback stays on the current action and enters **continuous mouse recovery**:

1. Release held buttons and modifier keys (this cancels an active drag).
2. Use an alternate movement method to move about 200 pixels within the same monitor, choosing the opposite direction near an edge.
3. Right-click once and press Escape to dismiss any context menu.
4. Return to the original destination using direct cursor positioning and resume the pending action only after reaching it.

If movement is still blocked, wait two seconds and repeat the recovery cycle. There is no attempt limit for mouse stalls; **F9** cancels recovery and playback. The account remains unfinished while recovery is active, and earlier recorded actions are not replayed. The status indicator shows the recovery count and latest error. If Windows rejects the recovery input itself, the recorder waits and retries rather than attempting more clicks in that cycle. Invalid recording coordinates and unrelated playback errors still report an error normally. Color waits use the same recovery and continue to require the selected color to match. No screenshot is sent by recovery.

The alternate method combines a small relative mouse event with checked [SetCursorPos](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setcursorpos) positioning. It avoids depending exclusively on the absolute mouse input path while still requiring the exact recorded destination.

Only one recorder from this folder may run at a time. When upgrading from an older version, close **all** existing recorder windows before opening **Start Recorder.bat**. Older copies do not have the single-instance guard and may compete for mouse input. Successful cursor movement confirms its position, not that the game accepted a click or finished loading.

## Recording history and management

Click **History / Manage** to see all saved recordings, with their names, saved dates, click counts, durations, and event counts. Every recording is saved as a separate entry when you finish recording. Previous entries are kept.

- **Save as…** saves the current sequence under a name as a new entry.
- **Load selected** (or double-click) makes a recording the current sequence. Press F7 separately to run it.
- **Rename…** changes the selected recording's name.
- **Delete** removes selected entries from the main list. Ctrl-click or Shift-click to select several.
- **Show deleted → Restore** brings deleted entries back.
- **Import… / Export…** lets you exchange recording JSON files.

Your existing saved recording files and backups are imported automatically once. They are not modified or removed by the import. The library lives in the `recordings` folder; deleted entries are kept in `recordings/deleted`. Deleting a library entry does not erase separately exported files or remove a copy already loaded for playback.

Recordings store the literal keys you type, including any passwords, in local JSON files. The original accounts.txt and code.txt are not modified.

Implementation uses the [pynput keyboard](https://pynput.readthedocs.io/en/latest/keyboard.html) and [mouse](https://pynput.readthedocs.io/en/latest/mouse.html) APIs. Screen colors use the Windows [GetPixel API](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-getpixel).
Mouse playback uses [SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput) with [virtual-desktop absolute coordinates](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-mouseinput) and verifies positions with GetCursorPos.
Screenshots use [Pillow ImageGrab](https://pillow.readthedocs.io/en/stable/reference/ImageGrab.html), and uploads use the [Telegram Bot API](https://core.telegram.org/bots/api#sendphoto).

Developer verification: `python -m unittest discover -s tests -v`.
Local desktop integration checks: `python tests/desktop_smoke.py` and `python tests/color_desktop_smoke.py` (close other recorder instances first).
Monitor and F3 integration check: `python tests/monitor_desktop_smoke.py`. Add `--send-test` only when you intend to send one full-monitor setup screenshot to the configured Telegram chat.
Live status integration check: `python tests/status_desktop_smoke.py`. Add `--send-test` to send one edited setup status (no screenshots) to the configured chat.
