// Test-only HTTP upstream. It never contacts OpenAI or the registry.
import { createServer } from "node:http";
import { readFileSync } from "node:fs";

const root = new URL("./fixtures/", import.meta.url);
const requests = new Map(JSON.parse(process.env.FIXTURE_QUERIES));
const citations = JSON.parse(readFileSync(new URL("citations.json", root), "utf8"));
let last;

createServer(async (request, response) => {
  response.setHeader("Content-Type", "application/json");

  if (request.url === "/health") return response.end("{}");

  if (request.url === "/last-request") return response.end(JSON.stringify(last));
  let raw = "";

  for await (const chunk of request) raw += chunk;
  const payload = JSON.parse(raw);

  last = payload;

  if (payload.query === "__disconnect") return request.socket.destroy();

  if (payload.query === "__malformed") return response.end('{"visualization":{"type":"unknown"}}');

  if (payload.query.startsWith("__error:")) {
    const code = payload.query.slice(8);

    response.statusCode =
      code === "request_timeout" ? 504 : code === "upstream_unavailable" ? 503 : 422;

    return response.end(
      JSON.stringify({
        error: {
          code,
          message:
            code === "citation_limit_exceeded"
              ? "Citation limit exceeded. Narrow the filters or disable citations."
              : `Request failed: ${code}.`,
        },
      }),
    );
  }

  let body = readFileSync(
    new URL(`responses/${requests.get(payload.query) || "phase_distribution"}.json`, root),
    "utf8",
  );

  if (payload.include_citations) body = JSON.stringify(citations.phase);

  if (payload.query === "__empty") {
    const result = JSON.parse(body);

    result.visualization.data = [];
    result.meta.retrieved_studies = result.meta.total_matches = 0;
    body = JSON.stringify(result);
  }

  if (payload.query === "__slow") await new Promise((resolve) => setTimeout(resolve, 800));
  response.end(body);
}).listen(9010, "127.0.0.1");
