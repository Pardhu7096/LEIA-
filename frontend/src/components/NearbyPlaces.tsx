import * as Linking from "expo-linking";
import { useEffect, useRef, useState } from "react";
import { Platform, Pressable, ScrollView, Text, View } from "react-native";

import { useAuth } from "../context/AuthContext";
import { styles } from "../styles/chat.styles";
import { tourStyles } from "../styles/tour.styles";

const categories = [
  ["schools", "Schools"],
  ["bus_stops", "Bus stops"],
  ["groceries", "Groceries"],
  ["hospitals", "Hospitals"],
  ["temples", "Temples"],
] as const;
type Category = typeof categories[number][0];
type Place = {
  id: string;
  name: string;
  address: string | null;
  directions_url: string;
  attributions: { provider?: string; providerUri?: string }[];
};
type Result = { status: "ready" | "missing_pin"; places: Place[] };

const embedKey = process.env.EXPO_PUBLIC_GOOGLE_MAPS_EMBED_KEY?.trim();

function directionsEmbedUrl(place: Place) {
  if (Platform.OS !== "web" || !embedKey) return null;
  const origin = new URLSearchParams(place.directions_url.split("?")[1]).get("origin");
  if (!origin) return null;
  const params = new URLSearchParams({
    key: embedKey,
    origin,
    destination: `place_id:${place.id}`,
  });
  return `https://www.google.com/maps/embed/v1/directions?${params}`;
}

export default function NearbyPlaces({ propertyId }: { propertyId: string }) {
  const { authFetch } = useAuth();
  const [category, setCategory] = useState<Category | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState("");
  const [openDirectionsId, setOpenDirectionsId] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);

  useEffect(() => () => request.current?.abort(), []);

  async function load(next: Category) {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setCategory(next);
    setResult(null);
    setOpenDirectionsId(null);
    setError("");
    try {
      const response = await authFetch(
        `/properties/${encodeURIComponent(propertyId)}/nearby?category=${next}`,
        { signal: controller.signal },
      );
      if (!response.ok) {
        const message = await response.json().catch(() => null);
        throw new Error(message?.detail ?? "Could not load nearby places.");
      }
      const data: Result = await response.json();
      if (!controller.signal.aborted) setResult(data);
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(cause instanceof Error ? cause.message : "Could not load nearby places.");
      }
    }
  }

  function openLink(url: string) {
    if (!url.startsWith("https://")) return;
    void Linking.openURL(url).catch(() => setError("Could not open the link."));
  }

  return (
    <ScrollView contentContainerStyle={{ padding: 20, gap: 12 }}>
      <Text accessibilityRole="header" style={styles.title}>Nearby places</Text>
      <Text style={styles.details}>Choose a category to search around this property.</Text>
      <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
        {categories.map(([id, label]) => (
          <Pressable key={id} accessibilityRole="button"
            accessibilityState={{ selected: category === id }}
            disabled={category === id && !result && !error}
            style={[tourStyles.button, category === id && { borderWidth: 1, borderColor: "#305B48" }]}
            onPress={() => void load(id)}>
            <Text style={tourStyles.buttonText}>{label}</Text>
          </Pressable>
        ))}
      </View>
      <Text accessibilityLiveRegion="polite" style={styles.details}>
        {error || (category && !result ? "Looking for nearby places…" :
          result?.status === "missing_pin" ? "This property doesn’t have a map pin yet." :
          result?.places.length === 0 ? "No places found in this category within 5 km." : "")}
      </Text>
      {!!result?.places.length && (
        <Text style={{ color: "#5E5E5E", fontSize: 14 }}>Google Maps</Text>
      )}
      {result?.places.map((place) => {
        const embedUrl = directionsEmbedUrl(place);
        const expanded = openDirectionsId === place.id;
        return (
          <View key={place.id} style={{ gap: 4, paddingVertical: 8 }}>
            <Text style={styles.title}>{place.name}</Text>
            {place.address && <Text style={styles.details}>{place.address}</Text>}
            {place.attributions.map(({ provider, providerUri }, index) =>
              provider && (providerUri?.startsWith("https://") ? (
                <Text key={index} accessibilityRole="link" style={styles.details} onPress={() => openLink(providerUri)}>
                  {provider}
                </Text>
              ) : <Text key={index} style={styles.details}>{provider}</Text>)
            )}
            {embedUrl ? (
              <>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={`${expanded ? "Hide" : "Show"} directions to ${place.name}`}
                  accessibilityState={{ expanded }}
                  style={tourStyles.button}
                  onPress={() => setOpenDirectionsId(expanded ? null : place.id)}>
                  <Text style={tourStyles.buttonText}>
                    {expanded ? "Hide directions" : "View directions here"}
                  </Text>
                </Pressable>
                {expanded && (
                  <iframe
                    src={embedUrl}
                    title={`Directions from this property to ${place.name}`}
                    referrerPolicy="strict-origin-when-cross-origin"
                    style={{ width: "100%", minWidth: 200, height: 360, border: 0 }}
                  />
                )}
              </>
            ) : Platform.OS === "web" ? (
              <a
                href={place.directions_url.startsWith("https://") ? place.directions_url : undefined}
                target="_self"
                aria-label={`Directions to ${place.name}`}
                style={{ display: "block", textDecoration: "none" }}>
                <View style={tourStyles.button}>
                  <Text style={tourStyles.buttonText}>Directions in Google Maps</Text>
                </View>
              </a>
            ) : (
              <Pressable accessibilityRole="link" accessibilityLabel={`Directions to ${place.name}`}
                style={tourStyles.button} onPress={() => openLink(place.directions_url)}>
                <Text style={tourStyles.buttonText}>Directions in Google Maps</Text>
              </Pressable>
            )}
          </View>
        );
      })}
    </ScrollView>
  );
}
