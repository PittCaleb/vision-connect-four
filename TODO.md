# TODO

## Perfect play from move 1 (optional C solver)

**Problem:** the pure-Python solver can't prove early-game positions in real
time, so the first third of a game gets best guesses. A first player who
follows them can still lose. This has happened in a real game.

**Plan:** add an optional compiled solver and keep the Python one, so there are
two paths:

- **With a C compiler:** a fast solver plus an opening book, giving proven
  suggestions from move 1.
- **Without one:** today's pure-Python solver. It works everywhere with no
  build step.

**Downside:** users who want perfect early play need a C compiler, such as Xcode
Command Line Tools on macOS, build-essential on Linux, or MSVC on Windows. They
would also need to run a build step. Users without a compiler must still get a
working app.

See "Optional C solver: design notes" in `AGENTS.md` for the approach.

## Ideas

- Linux and Windows text-to-speech (`espeak`, `pyttsx3`) for `--audio`.
- Automatic color calibration from the first frames of a game.
