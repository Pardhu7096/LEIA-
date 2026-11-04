import { useRef, useState, type ComponentRef } from "react";
import {
  Image,
  Modal,
  Pressable,
  ScrollView,
  Text,
  TextInput,
  useWindowDimensions,
  View,
} from "react-native";

import PanoramaView from "../components/PanoramaView";
import { useSavedChat, type Property } from "../hooks/useSavedChat";
import { styles } from "../styles/chat.styles";
import { historyStyles } from "../styles/history.styles";
import { tourStyles } from "../styles/tour.styles";

const SAMPLE_TOUR_URL =
  "https://cdn.pannellum.org/2.5/pannellum.htm" +
  "#panorama=" +
  encodeURIComponent("https://pannellum.org/images/alma.jpg") +
  "&autoLoad=true";

export default function HomeScreen() {
  const {
    messages,
    conversations,
    loading: saving,
    restoring,
    error,
    send,
    openConversation,
    newConversation,
  } = useSavedChat();

  const loading = saving || restoring;
  const { width } = useWindowDimensions();
  const isDesktop = width >= 900;

  const [input, setInput] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [selectedProperty, setSelectedProperty] =
    useState<Property | null>(null);

  const scrollRef = useRef<ComponentRef<typeof ScrollView>>(null);

  async function search() {
    const text = input.trim();

    if (!text || loading) return;

    const saved = await send(text);

    if (saved) setInput("");
  }

  function closeTour() {
    setSelectedProperty(null);
  }

  function newSearch() {
    if (loading) return;

    newConversation();
    setInput("");
    setHistoryOpen(false);
    closeTour();
  }

  function selectConversation(id: string) {
    if (loading) return;

    setInput("");
    closeTour();
    setHistoryOpen(false);
    void openConversation(id);
  }

  function renderHistory() {
    return (
      <ScrollView
        style={historyStyles.list}
        contentContainerStyle={historyStyles.listContent}
        keyboardShouldPersistTaps="handled"
      >
        {conversations.length === 0 && (
          <Text style={historyStyles.empty}>
            Your saved chats will appear here.
          </Text>
        )}

        {conversations.map((conversation) => (
          <Pressable
            key={conversation.id}
            onPress={() => selectConversation(conversation.id)}
            disabled={loading}
            accessibilityRole="button"
            accessibilityLabel={`Open saved chat: ${conversation.title}`}
            style={[
              historyStyles.item,
              loading && styles.buttonDisabled,
            ]}
          >
            <Text
              style={historyStyles.itemText}
              numberOfLines={2}
            >
              {conversation.title}
            </Text>
          </Pressable>
        ))}
      </ScrollView>
    );
  }

  return (
    <View style={styles.screen}>
      <View style={styles.header}>
        <Text style={styles.brand}>LEIA ✦</Text>

        {!isDesktop && (
          <Pressable
            onPress={() => setHistoryOpen(true)}
            accessibilityRole="button"
            accessibilityLabel="Open chat history"
            style={historyStyles.historyButton}
          >
            <Text style={historyStyles.buttonText}>
              History
            </Text>
          </Pressable>
        )}

        <Pressable
          onPress={newSearch}
          disabled={loading}
          accessibilityRole="button"
          accessibilityLabel="Start a new property search"
          style={[
            styles.newSearchButton,
            loading && styles.buttonDisabled,
          ]}
        >
          <Text style={styles.newSearchText}>
            ＋ New search
          </Text>
        </Pressable>
      </View>

      <View style={historyStyles.layout}>
        {isDesktop && (
          <View style={historyStyles.sidebar}>
            <Text style={historyStyles.heading}>
              Your conversations
            </Text>

            {renderHistory()}
          </View>
        )}

        <View style={historyStyles.main}>
          {!!error && (
            <Text
              style={styles.subtitle}
              accessibilityRole="alert"
            >
              {error}
            </Text>
          )}

          <ScrollView
            ref={scrollRef}
            style={styles.scroll}
            contentContainerStyle={styles.content}
            keyboardShouldPersistTaps="handled"
            onContentSizeChange={() => {
              scrollRef.current?.scrollToEnd({ animated: true });
            }}
          >
            {messages.length === 0 && !restoring && (
              <View style={styles.welcome}>
                <Text style={styles.eyebrow}>
                  YOUR PROPERTY COMPANION
                </Text>

                <Text style={styles.heading}>
                  Find your next home.
                </Text>

                <Text style={styles.subtitle}>
                  Tell me where you’d love to live.
                </Text>
              </View>
            )}

            {messages.map((message) => (
              <View key={message.id} style={styles.messageGroup}>
                {message.role === "assistant" && (
                  <Text style={styles.assistantLabel}>
                    LEIA ✦
                  </Text>
                )}

                <View
                  style={[
                    styles.message,
                    message.role === "user"
                      ? styles.userMessage
                      : styles.assistantMessage,
                  ]}
                >
                  <Text style={styles.messageText}>
                    {message.text}
                  </Text>
                </View>

                {!!message.properties?.length && (
                  <View style={styles.cards}>
                    {message.properties.map((property) => (
                      <View key={property.id} style={styles.card}>
                        <Image
                          source={{ uri: property.image }}
                          style={styles.photo}
                          accessibilityLabel={
                            `Illustrative stock photo for ${property.title}`
                          }
                        />

                        <View style={styles.cardBody}>
                          <Text style={styles.sample}>
                            SAMPLE LISTING
                          </Text>

                          <Text style={styles.price}>
                            {property.price}
                          </Text>

                          <Text style={styles.title}>
                            {property.title}
                          </Text>

                          <Text style={styles.subtitle}>
                            {property.location}
                          </Text>

                          <Text style={styles.details}>
                            {property.details}
                          </Text>

                          <Pressable
                            onPress={() => setSelectedProperty(property)}
                            accessibilityRole="button"
                            accessibilityLabel={
                              `Open sample 360-degree tour for ${property.title}`
                            }
                            style={tourStyles.button}
                          >
                            <Text style={tourStyles.buttonText}>
                              View sample 360° ↗
                            </Text>
                          </Pressable>
                        </View>
                      </View>
                    ))}
                  </View>
                )}
              </View>
            ))}

            {loading && (
              <Text
                style={styles.subtitle}
                accessibilityRole="alert"
              >
                {restoring
                  ? "Restoring your chat…"
                  : "LEIA is working…"}
              </Text>
            )}
          </ScrollView>

          <View style={styles.bottom}>
            <View style={styles.composer}>
              <TextInput
                value={input}
                onChangeText={setInput}
                editable={!loading}
                placeholder="Describe your ideal home…"
                placeholderTextColor="#879389"
                style={styles.input}
                accessibilityLabel="Your property search"
                returnKeyType="search"
                onSubmitEditing={search}
              />

              <Pressable
                onPress={search}
                disabled={loading || !input.trim()}
                accessibilityRole="button"
                accessibilityLabel="Search properties"
                style={[
                  styles.button,
                  (loading || !input.trim()) && styles.buttonDisabled,
                ]}
              >
                <Text style={styles.buttonText}>
                  {loading ? "Loading…" : "Search ↗"}
                </Text>
              </Pressable>
            </View>

            <Text style={styles.disclaimer}>
              AI responses may contain mistakes. Listings and photos are examples.
            </Text>
          </View>
        </View>
      </View>

      <Modal
        visible={historyOpen && !isDesktop}
        animationType="slide"
        onRequestClose={() => setHistoryOpen(false)}
      >
        <View style={historyStyles.drawer}>
          <View style={historyStyles.drawerHeader}>
            <Text style={historyStyles.drawerTitle}>
              Your conversations
            </Text>

            <Pressable
              onPress={() => setHistoryOpen(false)}
              accessibilityRole="button"
              accessibilityLabel="Close chat history"
              style={historyStyles.historyButton}
            >
              <Text style={historyStyles.buttonText}>
                Close ✕
              </Text>
            </Pressable>
          </View>

          {renderHistory()}
        </View>
      </Modal>

      <Modal
        visible={selectedProperty !== null}
        animationType="slide"
        onRequestClose={closeTour}
      >
        <View style={tourStyles.screen}>
          <View style={tourStyles.header}>
            <Text style={tourStyles.title}>
              {selectedProperty?.title ?? "Sample tour"}
            </Text>

            <Pressable
              onPress={closeTour}
              accessibilityRole="button"
              accessibilityLabel="Close 360-degree tour"
              style={tourStyles.close}
            >
              <Text style={tourStyles.closeText}>
                Close ✕
              </Text>
            </Pressable>
          </View>

          <Text style={tourStyles.notice}>
            Sample panorama — unrelated to this property.
          </Text>

          <View style={tourStyles.viewer}>
            {selectedProperty !== null && (
              <PanoramaView uri={SAMPLE_TOUR_URL} />
            )}
          </View>

          <Text style={tourStyles.help}>
            Drag to look around. Use + and − to zoom.
          </Text>
        </View>
      </Modal>
    </View>
  );
}