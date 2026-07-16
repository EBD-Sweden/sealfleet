import { NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { requirePlatformAdmin } from "@/lib/admin-auth";

export async function GET() {
  const { error } = await requirePlatformAdmin();
  if (error) return error;

  const result = await pool.query(
    `SELECT id, name, description, status FROM servers ORDER BY name`
  );
  return NextResponse.json(result.rows);
}
