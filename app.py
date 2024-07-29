from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
from itertools import product
from random import shuffle
import os

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})
socketio = SocketIO(app, cors_allowed_origins="*")

class Deck:
    def __init__(self):
        self.cards = []
        self.populate()
        self.shuffle_deck()

    def populate(self):
        suits = ["hearts", "clubs", "diamonds", "spades"]
        numbers = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "jack", "queen", "king", "ace"]
        self.cards = list(product(numbers, suits))
        self.cards.remove(('10', 'diamonds'))

    def shuffle_deck(self):
        shuffle(self.cards)

class Rounds:
    def __init__(self, Game):
        self.type = None
        self.rounds_played = []
        self.center_cards = []
        self.current_turn = None
        self.players_folds = {}
        self.played_cards = {}
        self.cards_played_by_all = set()
        self.game = Game
        self.scores = {}
        self.claims_received = set()
        self.initial_player_id = None
        self.first_fold = None
        self.last_fold = None
        self.available_round_types = set(self.round_types().keys())  # Track available round types

    def round_types(self):
        return {
            "Queens": {('queen', 'hearts'): 20, ('queen', 'diamonds'): 20, ('queen', 'clubs'): 20, ('queen', 'spades'): 20},
            "First and Last": 20,
            "Hearts": {('2', 'hearts'): 5, ('3', 'hearts'): 5, ('4', 'hearts'): 5, ('5', 'hearts'): 5, ('6', 'hearts'): 5, ('7', 'hearts'): 5, ('8', 'hearts'): 5, ('9', 'hearts'): 5, ('10', 'hearts'): 5, ('jack', 'hearts'): 5, ('queen', 'hearts'): 5, ('king', 'hearts'): 5, ('ace', 'hearts'): 5},
            "Folds":  5,
            "Barbu": {('king', 'spades'): 50},
            "Everything Everywhere all at once": 0
        }

    def set_round_type(self, round_type):
        self.type = round_type
        self.available_round_types.discard(round_type)  # Remove the selected round type from the available types
        print(f"Round type set to: {self.type}")
        if not self.available_round_types:
            self.game.is_last_round = True  # Mark this round as the last round


    def get_available_round_types(self):
        return list(self.available_round_types)

    def calculate_scores(self):
        types = self.round_types()
        if self.type == "Everything Everywhere all at once":
            self.calculate_folds(types)
            self.calculate_first_and_last(types)
            for type_round in ["Queens", "Hearts", "Barbu"]:
                for player_id, claims in self.players_folds.items():
                    for claim in claims:
                        for card in claim:
                            print(f"type {type_round}")
                            print(f"types in type {type_round in types}]")
                            if type_round in types and card in types[self.type]:
                                self.scores[player_id] += types[self.type][card]
        elif self.type == "Folds":
            self.calculate_folds(types)
        elif self.type == "First and Last":
            self.calculate_first_and_last(types)
        else:
            for player_id, claims in self.players_folds.items():
                for claim in claims:
                    for card in claim:
                        print(f"type {self.type}")
                        print(f"types in type {self.type in types}]")
                        if self.type in types and card in types[self.type]:
                            self.scores[player_id] += types[self.type][card]

    def calculate_folds(self, types):
        for player_id, claims in self.players_folds.items():
            for claim in claims:
                self.scores[player_id] += types[self.type]

    def calculate_first_and_last(self, types):
        self.scores[self.first_fold] += types[self.type]
        self.scores[self.last_fold] += types[self.type]

    def initial_player(self, player):
        self.initial_player_id = player

    def add_card_to_center(self, card):
        if card not in self.center_cards:
            self.center_cards.append(card)

    def claim_cards(self, player_id):
        claimed_cards = self.center_cards[:]
        if all(len(v) == 0 for v in self.players_folds.values()):
            self.first_fold = player_id
        elif all(len(v) == 0 for v in self.game.player_hands.values()):
            self.last_fold = player_id
        if player_id in self.players_folds:
            self.players_folds[player_id].append(claimed_cards)
        else:
            self.players_folds[player_id] = [claimed_cards]
        self.center_cards.clear()
        self.played_cards.clear()
        self.cards_played_by_all.clear()

        self.current_turn = player_id
        self.initial_player_id = player_id

        self.claims_received.add(player_id)

        socketio.emit('cards_claimed', {'player_id': player_id, 'claimed_cards': claimed_cards}, room='game_room')
        socketio.emit('update_center_cards', {'center_cards': self.center_cards}, room='game_room')

        if all(len(v) == 0 for v in self.game.player_hands.values()):
            print("All cards played, ending round...")
            self.game.end_round()

        return claimed_cards

    def end_round(self):
        self.calculate_scores()
        socketio.emit('round_over', {'scores': self.scores}, room='game_room')
        self.rounds_played.append(self.type)
        self.players_folds = {}
        self.center_cards = []
        self.cards_played_by_all = set()
        self.claims_received.clear()

        if self.game.is_last_round:
            self.game.end_game()  # End the game if this was the last round
        else:
            # Start a new round if it's not the last round
            self.game.deal_cards()
            socketio.emit('new_round', {'player_hands': self.game.player_hands, 'center_cards': self.center_cards, 'current_turn': self.current_turn}, room='game_room')



class Game:
    def __init__(self):
        self.deck = Deck()
        self.next_player_id = 1
        self.player_hands = {}
        self.players = {}
        self.Rounds = Rounds(self)
        self.is_last_round = False

    def add_player(self, sid):
        if sid not in self.players:
            self.players[sid] = None
            return True
        return False

    def assign_player_ids(self):
        for sid in self.players:
            if self.players[sid] is None:
                player_id = self.next_player_id
                self.next_player_id += 1
                self.players[sid] = player_id
                self.player_hands[player_id] = []
        print(f"Player IDs assigned: {self.players}")

    def deal_cards(self):
        self.deck = Deck()  # Reinitialize and shuffle the deck for a new round
        hands = {player_id: [] for player_id in self.player_hands}
        while self.deck.cards:
            for player_id in hands:
                if self.deck.cards:
                    hands[player_id].append(self.deck.cards.pop())
        self.player_hands = hands

    def play_card_centre(self, player_id, card):
        if self.Rounds.current_turn != player_id:
            print(f"Not {player_id}'s turn")
            return False

        if player_id in self.player_hands and card in self.player_hands[player_id]:
            self.player_hands[player_id].remove(card)
            if player_id not in self.Rounds.played_cards:
                self.Rounds.played_cards[player_id] = []
            self.Rounds.played_cards[player_id].append(card)
            self.Rounds.add_card_to_center(card)
            self.Rounds.cards_played_by_all.add(player_id)
            self.next_player(player_id)
            self.check_all_players_played()  # Check if all players have played a card
            return True
        return False

    def check_all_players_played(self):
        if len(self.Rounds.cards_played_by_all) == len(self.game.player_hands):
            socketio.emit('show_claim_button', room='game_room')

    def check_all_players_claimed(self):
        if len(self.Rounds.center_cards) == 0:
            print("game round end")
            self.end_round()
        else:
            socketio.emit('show_claim_button', room='game_room')


    def end_round(self):
        print("Ending round and calculating scores...")
        self.Rounds.end_round()
        self.player_hands = {pid: [] for pid in self.player_hands}
        socketio.emit('round_over', {'scores': self.Rounds.scores}, room='game_room')
        
        # Start a new round
        self.deal_cards()
        socketio.emit('new_round', {'player_hands': self.player_hands, 'center_cards': self.Rounds.center_cards, 'current_turn': self.Rounds.current_turn}, room='game_room')

    def check_all_players_played(self):
        if len(self.Rounds.cards_played_by_all) == len(self.player_hands):
            socketio.emit('show_claim_button', room='game_room')

    def next_player(self, current_player_id):
        player_ids = list(self.player_hands.keys())
        current_index = player_ids.index(current_player_id)
        self.Rounds.current_turn = player_ids[(current_index + 1) % len(player_ids)]

    def end_game(self):
        print("Game Over. Final scores:")
        socketio.emit('game_over', {'scores': self.Rounds.scores}, room='game_room')

    def reset_game(self):
        self.deck = Deck()
        self.next_player_id = 1
        self.player_hands = {}
        self.players = {}
        self.Rounds = Rounds(self)
        self.rounds_played = []
        self.is_last_round = False
        print("Game reset. Ready to start a new game.")
        
        socketio.emit('game_reset', room='game_room')

game = Game()
connected_clients = {}

@socketio.on('connect')
def handle_connect():
    join_room('game_room')
    if game.add_player(request.sid):
        pass
    connected_clients[request.sid] = None
    print(f"Client connected: {request.sid}. Total clients: {len(connected_clients)}")

@socketio.on('disconnect')
def handle_disconnect():
    leave_room('game_room')
    if request.sid in connected_clients:
        del connected_clients[request.sid]
        print(f"Client disconnected: {request.sid}. Total clients: {len(connected_clients)}")

@app.route('/restart_game', methods=['POST'])
def restart_game():
    game.reset_game()
    return jsonify({"status": "Game restarted"})

@app.route('/start_game', methods=['POST'])
def start_game():
    try:
        num_players = len(connected_clients)
        if num_players == 0:
            return jsonify({"status": "No players connected"}), 400

        game.__init__()
        for sid in connected_clients:
            game.add_player(sid)
        game.assign_player_ids()
        game.deal_cards()
        game.Rounds.current_turn = list(game.player_hands.keys())[0]
        game.Rounds.initial_player(game.Rounds.current_turn)
        game.Rounds.scores = {player_id: 0 for player_id in game.players.values()}

        for sid in connected_clients:
            socketio.emit('player_id', {'player_id': game.players[sid]}, room=sid)

        socketio.emit('game_started', {'player_hands': game.player_hands, 'players': game.players, 'center_cards': game.Rounds.center_cards, 'current_turn': game.Rounds.current_turn}, room='game_room')

        return jsonify({"status": "Game started", "player_hands": game.player_hands, 'players': game.players, 'center_cards': game.Rounds.center_cards})

    except Exception as e:
        print(f"Error starting game: {e}")
        return jsonify({"status": "Error", "message": str(e)}), 500

@app.route('/round_types', methods=['GET'])
def get_round_types():
    try:
        round_types = game.Rounds.round_types()
        return jsonify({'round_types': list(round_types.keys())})
    except Exception as e:
        print(f"Error fetching round types: {e}")
        return jsonify({"status": "Error", "message": str(e)}), 500

@socketio.on('start_round')
def handle_start_round(data):
    round_type = data.get('round_type')
    player_id = data.get('player_id')
    game.Rounds.current_turn = player_id
    game.Rounds.initial_player_id = player_id
    if not round_type:
        print("No round type selected.")
        return
    
    game.Rounds.set_round_type(round_type)
    available_round_types = game.Rounds.get_available_round_types()

    socketio.emit('round_type_selected', {'round_type': game.Rounds.type}, room='game_room')
    socketio.emit('update_round_types', {'round_types': available_round_types}, room='game_room')
    print(f"Round type set to: {game.Rounds.type}")

@socketio.on('play_card')
def handle_play_card(data):
    player_id = data.get('player_id')
    card = data.get('card')

    if player_id is None or card is None:
        print("Invalid card play data")
        return

    if isinstance(card, list):
        card = tuple(card)

    if game.play_card_centre(player_id, card):
        socketio.emit('card_played', {'player_id': player_id, 'card': card}, room='game_room')
        socketio.emit('update_player_hands', {'player_hands': game.player_hands}, room='game_room')
        socketio.emit('update_center_cards', {'center_cards': game.Rounds.center_cards}, room='game_room')
        socketio.emit('update_current_turn', {'current_turn': game.Rounds.current_turn}, room='game_room')
    else:
        print(f"Card {card} not found in player {player_id}'s hand or invalid action")

@socketio.on('claim_cards')
def handle_claim_cards(data):
    player_id = data.get('player_id')
    if player_id is None:
        print("Invalid claim data")
        return

    claimed_cards = game.Rounds.claim_cards(player_id)
    socketio.emit('cards_claimed', {'player_id': player_id, 'claimed_cards': claimed_cards}, room='game_room')
    socketio.emit('update_center_cards', {'center_cards': game.Rounds.center_cards}, room='game_room')

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), allow_unsafe_werkzeug=True)