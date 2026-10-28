import assert from "node:assert/strict";
import { lineDiff } from "../src/diff.js";

const unchanged = lineDiff("第一条\n设备一套。", "第一条\n设备一套。");
assert.deepEqual(
  unchanged.map((row) => row.type),
  ["same", "same"],
);

const changed = lineDiff("第一条\n设备一套。", "第一条\n设备两套。");
assert.deepEqual(
  changed.map((row) => row.type),
  ["same", "del", "add"],
);
assert.equal(changed[1].text, "设备一套。");
assert.equal(changed[2].text, "设备两套。");

const added = lineDiff("", "新增条款");
assert.deepEqual(added, [{ type: "add", text: "新增条款" }]);

console.log("diff tests passed");
