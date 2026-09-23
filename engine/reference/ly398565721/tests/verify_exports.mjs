import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const [outputDir, previewDir] = process.argv.slice(2);
await fs.mkdir(previewDir, { recursive: true });
for (const name of await fs.readdir(outputDir)) {
  if (!name.endsWith(".xlsx")) continue;
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(path.join(outputDir, name)));
  const sheets = await workbook.inspect({ kind: "sheet", include: "id,name" });
  const errors = await workbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 30 } });
  const sheetName = JSON.parse(sheets.ndjson.split("\n")[0]).name;
  const image = await workbook.render({ sheetName, autoCrop: "all", scale: 1, format: "png" });
  await fs.writeFile(path.join(previewDir, `${name}.png`), new Uint8Array(await image.arrayBuffer()));
  console.log(`${name}: ${sheets.ndjson.replaceAll("\n", " ")} | errors=${errors.ndjson}`);
}
