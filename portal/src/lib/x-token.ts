import { pool } from "@/lib/db";

export async function getValidToken(username?: string): Promise<string> {
  const result = username
    ? await pool.query(
        `SELECT * FROM x_accounts WHERE username = $1 ORDER BY updated_at DESC LIMIT 1`,
        [username.replace("@", "")]
      )
    : await pool.query(
        `SELECT * FROM x_accounts ORDER BY updated_at DESC LIMIT 1`
      );

  const account = result.rows[0];
  if (!account) {
    throw new Error("No connected X account found. Connect via the portal first.");
  }

  // Check if token needs refresh: expires_at within 5 minutes or null
  const needsRefresh =
    !account.expires_at ||
    new Date(account.expires_at).getTime() - Date.now() < 5 * 60 * 1000;

  if (!needsRefresh) {
    return account.access_token as string;
  }

  // Refresh the token
  if (!account.refresh_token) {
    throw new Error("X account token expired, please reconnect");
  }

  const clientId = process.env.X_CLIENT_ID;
  const clientSecret = process.env.X_CLIENT_SECRET;

  if (!clientId || !clientSecret) {
    throw new Error("X_CLIENT_ID and X_CLIENT_SECRET must be set");
  }

  const credentials = Buffer.from(`${clientId}:${clientSecret}`).toString("base64");

  const resp = await fetch("https://api.twitter.com/2/oauth2/token", {
    method: "POST",
    headers: {
      Authorization: `Basic ${credentials}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: new URLSearchParams({
      grant_type: "refresh_token",
      refresh_token: account.refresh_token as string,
    }),
  });

  if (!resp.ok) {
    const text = await resp.text();
    console.error("Token refresh failed:", text);
    throw new Error("X account token expired, please reconnect");
  }

  const data = await resp.json() as {
    access_token: string;
    refresh_token?: string;
    expires_in?: number;
  };

  const expiresAt = data.expires_in
    ? new Date(Date.now() + data.expires_in * 1000)
    : null;

  await pool.query(
    `UPDATE x_accounts SET access_token=$1, refresh_token=COALESCE($2, refresh_token), expires_at=$3, updated_at=NOW() WHERE id=$4`,
    [data.access_token, data.refresh_token ?? null, expiresAt, account.id]
  );

  return data.access_token;
}
