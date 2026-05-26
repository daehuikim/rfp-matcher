import { redirect } from "next/navigation";

// /upload 는 홈으로 통합됨 — 외부 링크 호환을 위해 리디렉트.
export default function UploadRedirect() {
  redirect("/");
}
