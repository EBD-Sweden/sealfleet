import { NextResponse } from "next/server";

export function GET() {
  return NextResponse.json(
    {
      status: "ready",
      service: "mcpfinder-portal",
      public: true,
      checks: {
        http: "ok",
      },
    },
    {
      status: 200,
      headers: {
        "Cache-Control": "no-store",
      },
    }
  );
}
