// Customer wording for builder and project facts (migration 014).
// Rules: unknown values read "Not provided"; every recorded value carries its source; a RERA number is
// shown only when LEIA verified it from documents (the server never sends an unverified one).

export type ProjectFactsInput = {
  builder?: string | null;
  project_name?: string | null;
  construction_status?: string | null;
  possession_date?: string | null;
  floor_number?: number | null;
  total_floors?: number | null;
  lift?: string | null;
  gated_community?: string | null;
  security?: string | null;
  amenities?: string[] | null;
  project_source?: string | null;
  rera_verified?: boolean | null;
  rera_number?: string | null;
};

export type Fact = { label: string; value: string };

const NOT_PROVIDED = "Not provided";
const SOURCES: Record<string, string> = {
  observed: "seen by agent",
  owner_reported: "owner reported",
  document_checked: "verified from documents",
};
const ANSWERS: Record<string, string> = { yes: "Yes", no: "No", not_applicable: "Not applicable" };
const SECURITY: Record<string, string> = {
  none: "None",
  guard: "Security guard",
  cctv: "CCTV",
  guard_and_cctv: "Security guard and CCTV",
  not_applicable: "Not applicable",
};
const AMENITIES: Record<string, string> = {
  gym: "Gym",
  pool: "Swimming pool",
  clubhouse: "Clubhouse",
  garden: "Garden",
  play_area: "Children's play area",
  community_hall: "Community hall",
};

/** Facts to show, or [] for listings from a server without migration 014 (nothing to say). */
export function projectFacts(input: ProjectFactsInput): Fact[] {
  if (!("lift" in input) && !("construction_status" in input) && !("rera_verified" in input)) return [];
  const source = input.project_source ? SOURCES[input.project_source] : undefined;
  const tag = (value: string) => (source ? `${value} (${source})` : value);
  const answer = (value?: string | null) => (value && ANSWERS[value] ? tag(ANSWERS[value]) : NOT_PROVIDED);

  let construction = NOT_PROVIDED;
  if (input.construction_status === "ready_to_move") construction = tag("Ready to move");
  else if (input.construction_status === "under_construction") {
    construction = tag(
      input.possession_date
        ? `Under construction, expected possession ${input.possession_date} (estimate)`
        : "Under construction",
    );
  } else if (input.construction_status === "not_applicable") construction = "Not applicable";

  const floor =
    typeof input.floor_number === "number"
      ? `${input.floor_number === 0 ? "Ground" : input.floor_number}${input.total_floors ? ` of ${input.total_floors}` : ""}`
      : input.total_floors
        ? `${input.total_floors} floors in building`
        : null;
  const amenities = (input.amenities ?? []).filter((a) => AMENITIES[a]).map((a) => AMENITIES[a]);

  return [
    { label: "Builder", value: input.builder || NOT_PROVIDED },
    ...(input.project_name ? [{ label: "Project", value: input.project_name }] : []),
    {
      label: "RERA",
      value: input.rera_verified && input.rera_number ? `${input.rera_number} (verified by LEIA)` : "Not verified",
    },
    { label: "Construction", value: construction },
    { label: "Floor", value: floor ? tag(floor) : NOT_PROVIDED },
    { label: "Lift", value: answer(input.lift) },
    { label: "Gated community", value: answer(input.gated_community) },
    {
      label: "Security",
      value: input.security && SECURITY[input.security] ? tag(SECURITY[input.security]) : NOT_PROVIDED,
    },
    { label: "Amenities", value: amenities.length ? tag(amenities.join(", ")) : NOT_PROVIDED },
  ];
}
