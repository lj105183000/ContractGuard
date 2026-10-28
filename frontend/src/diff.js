// Small line diff for revised-clause previews. LCS is fine for clause-sized
// text; this is a preview aid, not a full document diff engine.
export function lineDiff(originalText, revisedText) {
  if (!originalText) {
    return revisedText
      ? revisedText.split("\n").map((text) => ({ type: "add", text }))
      : [];
  }
  if (!revisedText) {
    return originalText.split("\n").map((text) => ({ type: "del", text }));
  }
  const left = originalText.split("\n");
  const right = revisedText.split("\n");
  const rows = left.length;
  const cols = right.length;
  const table = Array.from({ length: rows + 1 }, () =>
    new Array(cols + 1).fill(0),
  );
  for (let i = rows - 1; i >= 0; i -= 1) {
    for (let j = cols - 1; j >= 0; j -= 1) {
      table[i][j] =
        left[i] === right[j]
          ? table[i + 1][j + 1] + 1
          : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const changes = [];
  let i = 0;
  let j = 0;
  while (i < rows && j < cols) {
    if (left[i] === right[j]) {
      changes.push({ type: "same", text: left[i] });
      i += 1;
      j += 1;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      changes.push({ type: "del", text: left[i] });
      i += 1;
    } else {
      changes.push({ type: "add", text: right[j] });
      j += 1;
    }
  }
  while (i < rows) {
    changes.push({ type: "del", text: left[i] });
    i += 1;
  }
  while (j < cols) {
    changes.push({ type: "add", text: right[j] });
    j += 1;
  }
  return changes;
}
