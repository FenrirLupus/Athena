"""The instructions an agent is given, and the hive roster rule.

Their own module because both the manager and the router need them: `instructions_for` assembles the roster,
`route` reads the mention rule. Leaving them in `server/channels.py` and importing them from
`server/routing.py` would make the two modules import each other.

**Kept terse on purpose.** These are re-sent on *every* model call of every turn, tools included, so a word here
is paid many times over. Measured: the first versions cost 244 tokens (125 + 118 + roster); these cost ~150.
Every rule survives — identity deference, persistence, honesty, tool discipline, and all four parts of the
mention contract (syntax, queue-not-blocking, idle-vs-busy, address only listed names).
"""

# The identity is the profile's, not this string's: a channel whose ASSISTANT.md says "I am Scout" must not be
# told "You are Athena" by the instructions — the model then answers as the wrong agent (measured: scout
# replied in Athena's voice when the prompt named Athena and the identity file named Scout).
HIVE_INSTRUCTIONS = (
    "Hand work to another agent by writing @name: <question> in your reply. It queues on their channel and "
    "they answer when free — you are not blocked meanwhile. Prefer an IDLE peer for work that should start "
    "now; a BUSY one queues behind its current task. Address only these names. Peers: {roster}."
)

DEFAULT_INSTRUCTIONS = (
    "You are the agent in your identity block — that name, that role, that voice. You persist: your own "
    "thread, your own memory. Answer directly and honestly. Call a tool only for a fact you lack; short "
    "messages like *kisses* or 'hi' are conversation, so answer in your own voice, not with a tool. "
    "A Git repository URL is an intake request, not a task: call repo_inspect, follow the repo-intake skill, "
    "and propose an installation shape — never install one yourself. "
    "Never claim work you did not do."
)
