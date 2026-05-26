import { redirect } from "next/navigation";
import { NextRequest } from "next/server";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

export async function GET(req: NextRequest) {
  const sample = req.nextUrl.searchParams.get("sample");
  if (!sample) {
    redirect("/?error=sample-missing");
  }

  const r = await fetch(`${API_BASE}/documents/from-sample`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name: sample }),
    cache: "no-store",
  });

  if (!r.ok) {
    redirect("/?error=sample-start");
  }

  const { doc_id } = (await r.json()) as { doc_id: string };
  redirect(`/review/${doc_id}`);
}
