import { redirect } from "next/navigation";

// Root "/" redirects to portfolio page (the default dashboard landing)
export default function RootPage() {
  redirect("/portfolio");
}
