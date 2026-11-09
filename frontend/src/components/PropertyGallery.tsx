import { useEffect, useState } from "react";
import { Image, Pressable, SectionList, Text, View } from "react-native";

import { API_URL } from "../context/AuthContext";
import type { Property } from "../hooks/useSavedChat";
import { styles } from "../styles/chat.styles";
import { tourStyles } from "../styles/tour.styles";

type Photo = { id: string; room: string; image: string };
type Room = { title: string; data: Photo[] };

export default function PropertyGallery({ property }: { property: Property }) {
  const [rooms, setRooms] = useState<Room[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "unavailable" | "error">("loading");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch(`${API_URL}/properties/${encodeURIComponent(property.id)}/gallery`, {
          signal: controller.signal,
        });
        if (response.status === 404) {
          if (!controller.signal.aborted) setStatus("unavailable");
          return;
        }
        if (!response.ok) throw new Error("Could not load listing photos.");
        const photos: Photo[] = await response.json();
        const groups = new Map<string, Photo[]>();
        for (const photo of photos) {
          const entries = groups.get(photo.room);
          if (entries) entries.push(photo);
          else groups.set(photo.room, [photo]);
        }
        if (controller.signal.aborted) return;
        setRooms([...groups].map(([title, data]) => ({ title, data })));
        setStatus("ready");
      } catch {
        if (!controller.signal.aborted) setStatus("error");
      }
    }
    void load();
    return () => controller.abort();
  }, [property.id, attempt]);

  return (
    <SectionList
      sections={rooms}
      keyExtractor={(photo) => photo.id}
      contentContainerStyle={{ padding: 20, gap: 12 }}
      ListEmptyComponent={
        <View>
          {status === "ready" && property.image && (
            <Image source={{ uri: property.image }} accessibilityLabel="Listing cover photo" style={styles.photo} resizeMode="contain" />
          )}
          <Text style={styles.details} accessibilityLiveRegion="polite">
            {status === "loading" ? "Loading room photos…" :
              status === "unavailable" ? "This listing is no longer available." :
                status === "error" ? "Could not load room photos." : "No room photos have been added yet."}
          </Text>
          {status === "error" && (
            <Pressable accessibilityRole="button" style={tourStyles.button} onPress={() => {
              setStatus("loading");
              setAttempt((value) => value + 1);
            }}>
              <Text style={tourStyles.buttonText}>Try again</Text>
            </Pressable>
          )}
        </View>
      }
      renderSectionHeader={({ section }) => (
        <Text accessibilityRole="header" style={[styles.title, { backgroundColor: "#FFFDF8", paddingVertical: 8 }]}>
          {section.title}
        </Text>
      )}
      renderItem={({ item, index }) => (
        <Image source={{ uri: `${API_URL}${item.image}` }} accessibilityLabel={`${item.room} photo ${index + 1}`}
          style={{ width: "100%", height: 280, borderRadius: 12, backgroundColor: "#E8EEE1" }} resizeMode="contain" />
      )}
    />
  );
}
