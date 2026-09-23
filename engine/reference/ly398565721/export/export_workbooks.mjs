import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Sunday"];
const DAY_LABEL = { Monday: "周一", Tuesday: "周二", Wednesday: "周三", Thursday: "周四", Friday: "周五", Sunday: "周日" };
const DUTY_LABEL = { early: "早读", quiet: "静校", evening: "晚自习" };
const ROWS = ["早读", "第1节", "第2节", "第3节", "第4节", "静校", "第5节", "第6节", "第7节", "第8节", "晚自习"];
const COLORS = { navy: "#1F4E78", blue: "#D9EAF7", duty: "#FFF2CC", border: "#B7C9D6" };

function ranges(sheet, row, col, values) {
  const range = sheet.getRangeByIndexes(row, col, values.length, values[0].length);
  range.values = values;
  return range;
}

function title(sheet, text, columns) {
  const range = sheet.getRangeByIndexes(0, 0, 1, columns);
  range.merge();
  range.values = [[text]];
  range.format = { fill: COLORS.navy, font: { bold: true, color: "#FFFFFF", size: 14 }, horizontalAlignment: "center", verticalAlignment: "center" };
  range.format.rowHeight = 26;
}

function gridFormat(range, header = false) {
  range.format = {
    fill: header ? COLORS.blue : undefined,
    font: { bold: header },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: COLORS.border },
  };
}

function buildIndexes(schedule, duties) {
  const lessons = new Map();
  for (const item of schedule.lessons) lessons.set(`${item.class_id}|${item.day}|${item.period}`, item.subject);
  const teacherLessons = new Map();
  for (const item of schedule.lessons) teacherLessons.set(`${item.teacher}|${item.day}|${item.period}`, `${item.class_id}班 ${item.subject}`);
  const classDuties = new Map();
  const teacherDuties = new Map();
  for (const item of duties.assignments) {
    const subject = schedule.lessons.find((lesson) => lesson.class_id === item.class_id && lesson.teacher === item.teacher)?.subject ?? "值班";
    classDuties.set(`${item.class_id}|${item.day}|${item.duty_type}`, subject);
    teacherDuties.set(`${item.teacher}|${item.day}|${item.duty_type}`, `${item.class_id}班 ${subject}（${DUTY_LABEL[item.duty_type]}）`);
  }
  return { lessons, teacherLessons, classDuties, teacherDuties };
}

function rowValue(index, classId, day) {
  if (index === 0) return ["early", null];
  if (index >= 1 && index <= 4) return [null, index];
  if (index === 5) return ["quiet", null];
  if (index >= 6 && index <= 9) return [null, index - 1];
  return ["evening", null];
}

function writeDayBlock(sheet, startRow, startCol, day, classes, indexes) {
  ranges(sheet, startRow, startCol, [[DAY_LABEL[day], ...classes.map((id) => `${id}班`)]]);
  const header = sheet.getRangeByIndexes(startRow, startCol, 1, classes.length + 1);
  gridFormat(header, true);
  const body = ROWS.map((label, index) => {
    const [duty, period] = rowValue(index, "", day);
    return [label, ...classes.map((classId) => duty
      ? (indexes.classDuties.get(`${classId}|${day}|${duty}`) ?? "")
      : (indexes.lessons.get(`${classId}|${day}|${period}`) ?? ""))];
  });
  const bodyRange = ranges(sheet, startRow + 1, startCol, body);
  gridFormat(bodyRange);
  for (const row of [0, 5, 10]) sheet.getRangeByIndexes(startRow + 1 + row, startCol, 1, classes.length + 1).format.fill = COLORS.duty;
  sheet.getRangeByIndexes(startRow, startCol, ROWS.length + 1, classes.length + 1).format.columnWidth = 13;
  sheet.getRangeByIndexes(startRow, startCol, ROWS.length + 1, 1).format.columnWidth = 9;
}

function masterWorkbook(schedule, duties) {
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add("总课表");
  sheet.showGridLines = false;
  const classes = [...new Set(schedule.lessons.map((item) => item.class_id))];
  const indexes = buildIndexes(schedule, duties);
  title(sheet, "高一年级总课表（学科版）", classes.length + 1);
  ["Monday", "Tuesday", "Wednesday"].forEach((day, index) => writeDayBlock(sheet, 2, index * (classes.length + 3), day, classes, indexes));
  ["Thursday", "Friday", "Sunday"].forEach((day, index) => writeDayBlock(sheet, 15, index * (classes.length + 3), day, classes, indexes));
  return workbook;
}

function teacherWorkbook(schedule, duties) {
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add("教师个人课表");
  sheet.showGridLines = false;
  const indexes = buildIndexes(schedule, duties);
  const teachers = [...new Set(schedule.lessons.map((item) => item.teacher))].sort((a, b) => a.localeCompare(b, "zh"));
  title(sheet, "高一年级教师个人课表", 7);
  let row = 2;
  for (const teacher of teachers) {
    const subject = schedule.lessons.find((item) => item.teacher === teacher)?.subject ?? "";
    const heading = sheet.getRangeByIndexes(row, 0, 1, 7);
    heading.merge(); heading.values = [[`${subject}｜${teacher}`]];
    heading.format = { fill: COLORS.navy, font: { bold: true, color: "#FFFFFF" }, horizontalAlignment: "left" };
    row += 1;
    const matrix = [["时段", ...DAYS.map((day) => DAY_LABEL[day])]];
    ROWS.forEach((label, index) => {
      const [duty, period] = rowValue(index, "", "");
      matrix.push([label, ...DAYS.map((day) => duty
        ? (indexes.teacherDuties.get(`${teacher}|${day}|${duty}`) ?? "")
        : (indexes.teacherLessons.get(`${teacher}|${day}|${period}`) ?? ""))]);
    });
    const area = ranges(sheet, row, 0, matrix); gridFormat(area);
    sheet.getRangeByIndexes(row, 0, 1, 7).format.fill = COLORS.blue;
    for (const offset of [1, 6, 11]) sheet.getRangeByIndexes(row + offset, 0, 1, 7).format.fill = COLORS.duty;
    row += matrix.length + 1;
  }
  sheet.getRange("A:G").format.columnWidth = 17;
  sheet.freezePanes.freezeRows(2);
  return workbook;
}

function classWorkbook(schedule, duties) {
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add("各班课表");
  sheet.showGridLines = false;
  const classes = [...new Set(schedule.lessons.map((item) => item.class_id))];
  const indexes = buildIndexes(schedule, duties);
  title(sheet, "高一年级班级课表（仅学科）", 7);
  let row = 2;
  for (const classId of classes) {
    const heading = sheet.getRangeByIndexes(row, 0, 1, 7);
    heading.merge(); heading.values = [[`${classId}班`]];
    heading.format = { fill: COLORS.navy, font: { bold: true, color: "#FFFFFF" }, horizontalAlignment: "left" };
    row += 1;
    const matrix = [["时段", ...DAYS.map((day) => DAY_LABEL[day])]];
    ROWS.forEach((label, index) => {
      const [duty, period] = rowValue(index, classId, "");
      matrix.push([label, ...DAYS.map((day) => duty
        ? (indexes.classDuties.get(`${classId}|${day}|${duty}`) ?? "")
        : (indexes.lessons.get(`${classId}|${day}|${period}`) ?? ""))]);
    });
    const area = ranges(sheet, row, 0, matrix); gridFormat(area);
    sheet.getRangeByIndexes(row, 0, 1, 7).format.fill = COLORS.blue;
    for (const offset of [1, 6, 11]) sheet.getRangeByIndexes(row + offset, 0, 1, 7).format.fill = COLORS.duty;
    row += matrix.length + 1;
  }
  sheet.getRange("A:G").format.columnWidth = 15;
  sheet.freezePanes.freezeRows(2);
  return workbook;
}

function dutyWorkbook(duties) {
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add("值班表");
  sheet.showGridLines = false;
  title(sheet, "早读、静校、晚自习值班表", 4);
  const rows = [["日期", "班级", "值班类型", "教师姓名"], ...duties.assignments.map((item) => [DAY_LABEL[item.day] ?? item.day, `${item.class_id}班`, DUTY_LABEL[item.duty_type] ?? item.duty_type, item.teacher])];
  const range = ranges(sheet, 2, 0, rows); gridFormat(range);
  sheet.getRangeByIndexes(2, 0, 1, 4).format.fill = COLORS.blue;
  sheet.getRange("A:D").format.columnWidth = 18;
  sheet.freezePanes.freezeRows(3);
  return workbook;
}

export async function buildAllWorkbooks({ schedulePath, dutyPath, outputDir }) {
  const schedule = JSON.parse(await fs.readFile(schedulePath, "utf8"));
  const duties = JSON.parse(await fs.readFile(dutyPath, "utf8"));
  if (!Array.isArray(schedule.lessons) || !Array.isArray(duties.assignments)) throw new Error("排课JSON格式不正确");
  await fs.mkdir(outputDir, { recursive: true });
  const builds = [
    ["总表学科版.xlsx", masterWorkbook(schedule, duties)],
    ["教师个人课表.xlsx", teacherWorkbook(schedule, duties)],
    ["班级课表.xlsx", classWorkbook(schedule, duties)],
    ["早读静校晚自习值班表.xlsx", dutyWorkbook(duties)],
  ];
  const results = [];
  for (const [fileName, workbook] of builds) {
    const file = await SpreadsheetFile.exportXlsx(workbook);
    const filePath = path.join(outputDir, fileName);
    await file.save(filePath);
    results.push(filePath);
  }
  return results;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [schedulePath, dutyPath, outputDir] = process.argv.slice(2);
  if (!schedulePath || !dutyPath || !outputDir) throw new Error("用法：node export_workbooks.mjs <schedule.json> <duties.json> <output-dir>");
  const results = await buildAllWorkbooks({ schedulePath, dutyPath, outputDir });
  console.log(JSON.stringify(results));
}
