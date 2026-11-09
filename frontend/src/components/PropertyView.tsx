import { useRef, useState, type ComponentRef } from "react";
import {
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  Text,
  TextInput,
  View,
} from "react-native";

import NearbyPlaces from "./NearbyPlaces";
import PanoramaView from "./PanoramaView";
import PropertyGallery from "./PropertyGallery";
import type { Message, Property } from "../hooks/useSavedChat";
import { styles } from "../styles/chat.styles";
import { tourStyles } from "../styles/tour.styles";

const tabs = [
  { id: "photos", label: "Photos" },
  { id: "tour", label: "360° Tour" },
  { id: "nearby", label: "Nearby Places" },
  { id: "ask", label: "Ask LEIA" },
] as const;

const sampleTour =
  "https://cdn.pannellum.org/2.5/pannellum.htm" +
  "#panorama=https%3A%2F%2Fcdn.pannellum.org%2F2.5%2Fimages%2Falma.jpg&autoLoad=true";

type Props = {
  property: Property;
  messages: Message[];
  loading: boolean;
  error: string;
  onAsk: (question: string, propertyId: string) => Promise<boolean>;
  onClose: () => void;
};

export default function PropertyView({ property, messages, loading, error, onAsk, onClose }: Props) {
  const [activeTab, setActiveTab] = useState<(typeof tabs)[number]["id"]>(
    property.isSample ? "tour" : "photos",
  );
  const [input, setInput] = useState("");
  const [nearbyOpened, setNearbyOpened] = useState(false);
  const scrollRef = useRef<ComponentRef<typeof ScrollView>>(null);
  const tour = property.isSample ? sampleTour : property.tour;

  async function ask() {
    const question = input.trim();
    if (!question || loading || property.isSample) return;
    if (await onAsk(question, String(property.id))) setInput("");
  }

  return (
    <Modal visible animationType="slide" onRequestClose={onClose}>
      <View style={tourStyles.screen}>
        <View style={tourStyles.header}>
          <View style={tourStyles.propertySummary}>
            <Text accessibilityRole="header" style={tourStyles.title}>{property.title}</Text>
            <Text style={styles.subtitle}>{[property.price, property.location].filter(Boolean).join(" · ")}</Text>
            <Text style={tourStyles.summaryDetails}>{property.details}</Text>
          </View>
          <Pressable onPress={onClose} accessibilityRole="button" accessibilityLabel="Close property"
            style={tourStyles.close}>
            <Text style={tourStyles.closeText}>Close ✕</Text>
          </Pressable>
        </View>

        {!property.isSample ? (
          <View style={tourStyles.propertyTabs}>
            {tabs.map(({ id, label }) => (
              <Pressable key={id} accessibilityRole="button"
                accessibilityState={{ selected: activeTab === id }}
                style={[tourStyles.close, activeTab === id && tourStyles.selectedTab]}
                onPress={() => {
                  setActiveTab(id);
                  if (id === "nearby") setNearbyOpened(true);
                }}>
                <Text style={tourStyles.closeText}>{label}</Text>
              </Pressable>
            ))}
          </View>
        ) : null}

        {!property.isSample ? (
          <View style={activeTab === "photos" ? tourStyles.viewerArea : tourStyles.hidden}>
            <PropertyGallery property={property} />
          </View>
        ) : null}

        {activeTab === "tour" ? (
          tour ? (
            <>
              {property.isSample ? (
                <Text style={tourStyles.notice}>Sample panorama — unrelated to this property.</Text>
              ) : null}
              <View style={tourStyles.viewer}>
                <PanoramaView uri={tour} />
              </View>
              <Text style={tourStyles.help}>Drag to look around. Use + and − to zoom.</Text>
            </>
          ) : <Text style={tourStyles.notice}>A 360° tour hasn’t been added for this property.</Text>
        ) : null}

        {nearbyOpened ? (
          <View style={activeTab === "nearby" ? tourStyles.viewerArea : tourStyles.hidden}>
            <NearbyPlaces propertyId={String(property.id)} />
          </View>
        ) : null}

        {activeTab === "ask" ? (
          <KeyboardAvoidingView style={tourStyles.viewerArea}
            behavior={Platform.OS === "ios" ? "padding" : undefined}>
            <ScrollView ref={scrollRef} style={styles.scroll} contentContainerStyle={styles.content}
              keyboardShouldPersistTaps="handled"
              onContentSizeChange={() => scrollRef.current?.scrollToEnd({ animated: true })}>
              {messages.length === 0 ? (
                <Text style={styles.subtitle}>Ask about the size, price, parking, or nearby places.</Text>
              ) : null}
              {messages.map((message) => (
                <View key={message.id} style={[
                  styles.message,
                  message.role === "user" ? styles.userMessage : styles.assistantMessage,
                ]}>
                  <Text style={styles.messageText}>{message.text}</Text>
                </View>
              ))}
              {loading ? <Text accessibilityLiveRegion="polite" style={styles.subtitle}>LEIA is working…</Text> : null}
              {error ? <Text accessibilityRole="alert" style={styles.subtitle}>{error}</Text> : null}
            </ScrollView>

            <View style={styles.bottom}>
              <View style={styles.composer}>
                <TextInput value={input} onChangeText={setInput} editable={!loading}
                  placeholder="Ask about this property…" placeholderTextColor="#879389"
                  style={styles.input} accessibilityLabel={`Question about ${property.title}`}
                  returnKeyType="send" onSubmitEditing={ask} />
                <Pressable onPress={ask} disabled={loading || !input.trim()} accessibilityRole="button"
                  style={[styles.button, (loading || !input.trim()) && styles.buttonDisabled]}>
                  <Text style={styles.buttonText}>Ask</Text>
                </Pressable>
              </View>
            </View>
          </KeyboardAvoidingView>
        ) : null}
      </View>
    </Modal>
  );
}
