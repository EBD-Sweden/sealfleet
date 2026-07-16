import { NextRequest, NextResponse } from "next/server";
import { requirePortalSession } from "@/lib/portal-auth";

const RUNTIME_URL = process.env.RUNTIME_URL || "http://localhost:8040";

// Public example: run the weather_trip_planner v2 pipeline and return its
// output for the /weather-trip dashboard to visualize.
export async function POST(req: NextRequest) {
  const { error, context } = await requirePortalSession();
  if (error) return error;

  try {
    const body = (await req.json().catch(() => ({}))) as {
      cities?: string[];
      target_temp_c?: number;
      max_wind_kph?: number;
    };

    const inputs: Record<string, unknown> = {};
    if (Array.isArray(body.cities) && body.cities.length > 0) {
      inputs.cities = body.cities.slice(0, 8).map((c) => String(c));
    }
    if (typeof body.target_temp_c === "number") inputs.target_temp_c = body.target_temp_c;
    if (typeof body.max_wind_kph === "number") inputs.max_wind_kph = body.max_wind_kph;

    const res = await fetch(`${RUNTIME_URL}/v2/pipelines/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...context?.backendHeaders },
      body: JSON.stringify({ pipeline: "weather_trip_planner", inputs }),
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "proxy error" },
      { status: 502 }
    );
  }
}
