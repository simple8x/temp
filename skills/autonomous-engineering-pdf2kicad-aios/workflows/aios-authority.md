# AIOS authority and durable execution workflow

## Authority chain
`DECISION → EXECUTE → COMMIT → MEMORY_WRITE`

Every transition must bind:
- explicit predecessor and predecessor evidence;
- exact capability and scope;
- permit/authority reference;
- stable lineage/correlation ID;
- observable result and receipt.

Fail closed when policy, registry, issuer, or permit evidence is missing, stale, or mismatched. Authentication and token presence are not authority.

## Durable effect lifecycle
`contract → permit → effect → dispatch → execute_attempt → receipt → OBSERVED | UNKNOWN`

- effect_id identifies the intended side effect and remains stable across valid retries.
- attempt_id uniquely identifies each execution attempt.
- Record provider, capability, input hash, timestamps, result, output hash, and lineage.
- Distinguish prepared, dispatched, attempted, observed, and unknown states.
- A queued retry is not DISPATCHED.
- If a remote effect may have happened but cannot be observed, persist UNKNOWN and reconcile before retrying.
- Retry only when idempotency is proven or provider reconciliation establishes safe retry.
- A draft PR is not a merge; a merge is not a deployment; a deployment request is not live verification.

## Stage gates
`Contract → Plan → Execute → Observe → Verify → Reconcile → Promote/Block → Persist`

For every stage define inputs, permitted operations, evidence, acceptance criteria, and recovery behavior. Promotion requires independent verification. Keep last-known-good state when blocked.

## Provider constraints
- Use only user-authorized and configured providers/control planes.
- Gemini-only means do not silently fall back to another model provider.
- AIOS is an authority and evidence workflow, not an assumed runtime/browser agent.
- Scope adapters narrowly. Do not activate a capability because it appears in code or a registry draft.
- Treat unavailable quota, absent secret, unauthorized endpoint, and incomplete deployment as explicit blockers.

## Recovery
1. Read the durable state and latest receipt.
2. Determine whether the last attempt was never dispatched, dispatched, observed, or UNKNOWN.
3. Reconcile with the authoritative provider before retrying uncertain effects.
4. Create a new attempt ID for a retry; retain the original effect ID only if it is the same logical effect.
5. Never rewrite history to make a failed attempt appear successful.
6. Preserve evidence and record the reason for any block, compensation, or promotion.
