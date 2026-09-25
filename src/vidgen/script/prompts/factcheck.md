You are fact-checking a short video script against its reference material.

Reference material:
$facts

Script scenes:
$scenes

For every scene that states a factual claim which the reference material does NOT support, or which
contradicts it (wrong number, reversed cause/effect, wrong "only/never/always", invented person, date or
event), report it. Ignore questions, opinions, calls to comment and general phrasing that asserts nothing.
- `id`: the scene number.
- `note`: in $lang_name, one short sentence saying what is wrong or unsupported and, if the material
  says otherwise, what it says.
Return an empty list if every factual claim is supported.
