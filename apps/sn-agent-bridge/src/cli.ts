import { createAgentBridge } from "./index.js";
import { encodeResponse, executeRequest, inputFailure, MAX_INPUT_BYTES } from "./cli-protocol.js";

async function main(): Promise<void> {
  let response;
  try {
    const chunks: Buffer[] = [];
    let length = 0;
    for await (const chunk of process.stdin) {
      const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      length += bytes.length;
      if (length > MAX_INPUT_BYTES) throw Error("input bound");
      chunks.push(bytes);
    }
    const text = new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks));
    response = await executeRequest(JSON.parse(text), createAgentBridge);
  } catch {
    process.stdin.destroy();
    response = inputFailure();
  }
  // Success, missing and dependency failures all use the envelope. The future
  // Python caller must inspect result AND release; timeout belongs to that caller.
  process.stdout.write(encodeResponse(response));
}

process.stdout.on("error", () => { process.exitCode = 1; });
void main();
