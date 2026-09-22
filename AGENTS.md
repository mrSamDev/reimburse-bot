Write code people can maintain at 2am.

## Tooling

- Use **pnpm** for all node package management. No npm/yarn. Scripts run via `pnpm <script>` (e.g. `pnpm test`, `pnpm dev`).

## Core Rules

- Prefer simple code over clever code.
- Make state and side effects explicit.
- One file, one responsibility.
- One function, one job.
- Delete abstraction until the code becomes harder to change.
- Comments explain why, not what.
- Optimize for readability first, performance second.

## Anti-Bloat Rules

Avoid:

- Deep abstraction layers
- Generic utility wrappers with one caller
- Premature optimization
- Enterprise naming (`BaseManagerFactoryService`)
- Config-driven code when plain code is clearer
- Large "reusable" components that solve hypothetical problems
- Overusing classes when functions work
- AI filler words and long explanations

Prefer:

- Small pure functions
- Flat control flow
- Early returns
- Explicit data flow
- Composition over inheritance
- Simple modules with obvious names

If a junior engineer cannot trace the flow in under 2 minutes, simplify it.

## File Rules

- Target under 200 lines per file.
- Split by responsibility, not file type.
- Files that change together belong together.
- Avoid dumping unrelated helpers into `utils.ts`.

Bad:

```ts
// utils.ts
export function formatDate() {}
export function validateEmail() {}
export function calculateTax() {}
```

Good:

```ts
// date.ts
export function formatDate() {}

// validation.ts
export function validateEmail() {}
```

## Function Rules

Functions should:

- Do one thing
- Be easy to test
- Avoid hidden state
- Stay under ~50 lines
- Prefer deterministic input/output

Bad:

```ts
let cache = {};

function getUser(id) {
  if (!cache[id]) {
    cache[id] = db.users.find(id);
  }

  return cache[id];
}
```

Good:

```ts
function getUser(users, id) {
  return users.find((user) => user.id === id);
}
```

Prefer early returns over nested conditionals.

Bad:

```ts
function process(user) {
  if (user) {
    if (user.isActive) {
      return save(user);
    }
  }
}
```

Good:

```ts
function process(user) {
  if (!user || !user.isActive) {
    return;
  }

  return save(user);
}
```

## Naming

Names should explain intent without comments.

Bad:

```ts
const data = getData();
const temp = user.email;
const flag = true;
```

Good:

```ts
const users = getUsers();
const userEmail = user.email;
const isProcessing = true;
```

Boolean names should read naturally:

```ts
if (isValid && hasAccess && !isLoading) {
```

Avoid abbreviations unless standard:

- good: `url`, `html`, `db`
- bad: `usr`, `cfg`, `respData`

## Comments

Most comments are failed naming.

Do not comment obvious code.

Bad:

```ts
// increment count
count++;
```

Good:

```ts
// Stripe retries old webhook events for up to 3 days
```

Comment only when explaining:

- Business rules
- Security constraints
- Performance tradeoffs
- Library/framework workarounds
- Non-obvious decisions

## AI Writing Filter

Remove words like:

- robust
- scalable
- seamless
- comprehensive
- optimized
- leverage
- facilitate
- enterprise-grade

Avoid:

- Long introductions
- Repeating the same point
- Explaining obvious code
- Decorative architecture language
- Huge docblocks

Bad:

```ts
/**
 * This function handles comprehensive validation
 * and ensures robust processing of user input.
 */
```

Good:

```ts
// API accepts partial updates
```

## Error Handling

Fail fast.

Do not swallow errors.

Bad:

```ts
try {
  return await fetchUsers();
} catch (error) {
  console.log(error);
  return [];
}
```

Good:

```ts
try {
  return await fetchUsers();
} catch (error) {
  throw new Error(`Failed to fetch users: ${error.message}`);
}
```

## Types

Use TypeScript strictly.

- Prefer `unknown` over `any`
- Validate at boundaries
- Trust internal types after validation
- Encode invalid states out of existence when possible

Bad:

```ts
function parse(data: any) {}
```

Good:

```ts
function parse(data: unknown) {}
```

## Async Rules

Use `async/await`.

Run independent work in parallel.

```ts
const [user, settings] = await Promise.all([getUser(id), getSettings(id)]);
```

Avoid unnecessary awaits inside loops.

## Testing

Test behavior, not implementation.

Bad:

```ts
expect(service.validate).toHaveBeenCalled();
```

Good:

```ts
expect(() => createUser({})).toThrow();
```

Pure functions need fewer mocks. That is usually a sign the design is better.

## Review Checklist

Before merging:

- File under 200 lines?
- Functions focused?
- Hidden state removed?
- Names obvious?
- Comments necessary?
- Any AI filler language?
- Any abstraction with only one caller?
- Any reusable code that is not reused?
- Error paths clear?
- Types strict?
- Tests cover behavior?

If removing code makes the design clearer, remove it.