import { NextResponse } from "next/server";
import { verifyPassword } from "@/lib/password";
import { createSession } from "@/lib/session";

export async function POST(req: Request) {
  let password = "";
  try {
    const body = await req.json();
    password = typeof body?.password === "string" ? body.password : "";
  } catch {
    return NextResponse.json({ error: "invalid body" }, { status: 400 });
  }

  if (!password || !verifyPassword(password)) {
    // uniform response — no distinction between "no password" and "wrong password"
    return NextResponse.json({ error: "invalid credentials" }, { status: 401 });
  }

  await createSession();
  return NextResponse.json({ ok: true });
}
