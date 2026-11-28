# What is in this folder

Nothing is added yet: this is the general agent (Claude Code) as it is.

- `start`: launches Claude Code with the arguments and the task instruction it is given.
  It also wires three places into the launch; all three are empty, so they change nothing.
  - `CLAUDE.md`: appended to the system prompt when not empty.
  - `settings.json`: passed as Claude Code settings when not empty.
  - `bin/`: put on the agent's PATH.
