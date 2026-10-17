import { NextRequest } from "next/server";
import { handle } from "../../../../lib/server-proxy";

async function dispatch(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  return handle(request, (await context.params).path);
}
export {
  dispatch as GET,
  dispatch as POST,
  dispatch as DELETE,
  dispatch as HEAD,
};
