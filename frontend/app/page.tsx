import HomePageClient from "@/components/HomePageClient";
import { fetchSamplesServer } from "@/lib/samples-server";

type Props = {
  searchParams: Promise<{ error?: string }>;
};

export default async function HomePage({ searchParams }: Props) {
  const params = await searchParams;
  const samples = await fetchSamplesServer();
  return <HomePageClient initialSamples={samples} startError={params.error} />;
}
