# -*- coding: utf-8 -*-
"""
Created on Sat May 30 22:45:37 2020

@author: alial
"""


import numpy as np
import pandas as pd

from ib_insync import *
import nest_asyncio
import talib as ta
from tensorflow.keras.models import Model, load_model
from tensorflow.keras.layers import Dense, Input, Dropout, LSTM, GlobalAveragePooling1D
from tensorflow.keras.optimizers import Adam

from datetime import datetime, timedelta
import itertools
import os
import pickle

from sklearn.preprocessing import StandardScaler





def maybe_make_dir(directory):
  if not os.path.exists(directory):
    os.makedirs(directory)



class get_data:
        
    def next_exp_weekday(self):
        weekdays = {2: [5, 6, 0], 4: [0, 1, 2], 0: [3, 4]}
        today = datetime.today().weekday()
        for exp, day in weekdays.items():
            if today in day:
                return exp
    
    def next_weekday(self, d, weekday):
    
        days_ahead = weekday - d.weekday()
        if days_ahead <= 0:  # Target day already happened this week
            days_ahead += 7
        date_to_return = d + timedelta(days_ahead)  # 0 = Monday, 1=Tuself.ESday, 2=Wednself.ESday...
        return date_to_return.strftime('%Y%m%d')
    
    def get_strikes_and_expiration(self):
        ES = Future(symbol='ES', lastTradeDateOrContractMonth='20200918', exchange='GLOBEX',
                                currency='USD')
        ib.qualifyContracts(ES)
        expiration = self.next_weekday(datetime.today(), self.next_exp_weekday())
        chains = ib.reqSecDefOptParams(underlyingSymbol='ES', futFopExchange='GLOBEX', underlyingSecType='FUT',underlyingConId=ES.conId)
        chain = util.df(chains)
        strikes = chain[chain['expirations'].astype(str).str.contains(expiration)].loc[:, 'strikes'].values[0]
        [ESValue] = ib.reqTickers(ES)
        ES_price= ESValue.marketPrice()
        strikes = [strike for strike in strikes
                if strike % 5 == 0
                and ES_price - 10 < strike < ES_price + 10]
        return strikes,expiration
    
    def get_contract(self, right, net_liquidation):
        strikes, expiration=self.get_strikes_and_expiration()
        for strike in strikes:
            contract=FuturesOption(symbol='ES', lastTradeDateOrContractMonth=expiration, 
                                                strike=strike,right=right,exchange='GLOBEX')
            ib.qualifyContracts(contract)
            price = ib.reqMktData(contract,"",False,False)
            if float(price.last)*50 >=net_liquidation:
                continue
            else:
                return contract
    
    
    
    def ES(self):
        ES = Future(symbol='ES', lastTradeDateOrContractMonth='20200918', exchange='GLOBEX',
                                currency='USD')
        ib.qualifyContracts(ES)
        ES_df = ib.reqHistoricalData(contract=ES, endDateTime=endDateTime, durationStr=No_days,
                                     barSizeSetting=interval, whatToShow = 'TRADES', useRTH = False)
        ES_df = util.df(ES_df)
        ES_df.set_index('date',inplace=True)
        ES_df['RSI'] = ta.RSI(ES_df['close'])
        ES_df['macd'],ES_df['macdsignal'],ES_df['macdhist'] = ta.MACD(ES_df['close'], fastperiod=12, slowperiod=26, signalperiod=9)
        ES_df['MA_9']=ta.MA(ES_df['close'], timeperiod=9)
        ES_df['MA_21']=ta.MA(ES_df['close'], timeperiod=21)
        ES_df['MA_200']=ta.MA(ES_df['close'], timeperiod=200)
        ES_df['EMA_9']=ta.EMA(ES_df['close'], timeperiod=9)
        ES_df['EMA_21']=ta.EMA(ES_df['close'], timeperiod=21)
        ES_df['EMA_50']=ta.EMA(ES_df['close'], timeperiod=50)
        ES_df['EMA_200']=ta.EMA(ES_df['close'], timeperiod=200)
        ES_df['ATR']=ta.ATR(ES_df['high'],ES_df['low'], ES_df['close'])
        ES_df['roll_max_cp']=ES_df['high'].rolling(20).max()
        ES_df['roll_min_cp']=ES_df['low'].rolling(20).min()
        ES_df['roll_max_vol']=ES_df['volume'].rolling(20).max()
        ES_df['EMA_21-EMA_9']=ES_df['EMA_21']-ES_df['EMA_9']
        ES_df['EMA_200-EMA_50']=ES_df['EMA_200']-ES_df['EMA_50']
        
        ES_df.dropna(inplace = True)
        return ES_df
    
    def option_history(self, contract):
        df = pd.DataFrame(util.df(ib.reqHistoricalData(contract=contract, endDateTime=endDateTime, durationStr=No_days,
                                      barSizeSetting=interval, whatToShow = 'MIDPOINT', useRTH = False, keepUpToDate=False))[['date','close']])
        df.columns=['date',f"{contract.symbol}_{contract.right}_close"]
        df.set_index('date',inplace=True)
        return df
    
    def options(self, df1,df2):
        return pd.merge(df1,df2, on='date', how='outer').dropna()

# =============================================================================
# start machine learning
# =============================================================================

### The experience replay memory ###




def get_scaler(env):
      # return scikit-learn scaler object to scale the states
      # Note: you could also populate the replay buffer here
    
      states = []
      for _ in range(env.n_step):
        action = np.random.choice(env.action_space)
        state, reward, done, info = env.step(action)
        states.append(state)
        if done:
          break
    
      scaler = StandardScaler()
      scaler.fit(states)
      return scaler




def maybe_make_dir(directory):
      if not os.path.exists(directory):
          os.makedirs(directory)
    



def mlp(input_dim, n_action, n_hidden_layers=2, hidden_dim=5):
    """ A multi-layer perceptron """
     
    # input layer
    i = Input(shape=(input_dim,1))
    x = i
     
    # hidden layers
    for _ in range(n_hidden_layers):
      x = Dropout(0.2)(x)
      x = LSTM(hidden_dim, return_sequences = True)(x)
     
    x = GlobalAveragePooling1D()(x)
    # final layer
    x = Dense(n_action, activation='relu')(x)
    x = Dense(n_action, activation='softmax')(x)
    # make the model
    model = Model(i, x)
     
    model.compile(loss='categorical_crossentropy', optimizer='adam')
    print((model.summary()))
    return model


class ReplayBuffer:
      def __init__(self, obs_dim, act_dim, size):
        self.obs1_buf = np.zeros([size, obs_dim], dtype=np.float32)
        self.obs2_buf = np.zeros([size, obs_dim], dtype=np.float32)
        self.acts_buf = np.zeros(size, dtype=np.uint8)
        self.rews_buf = np.zeros(size, dtype=np.float32)
        self.done_buf = np.zeros(size, dtype=np.uint8)
        self.ptr, self.size, self.max_size = 0, 0, size
    
      def store(self, obs, act, rew, next_obs, done):
        self.obs1_buf[self.ptr] = obs
        self.obs2_buf[self.ptr] = next_obs
        self.acts_buf[self.ptr] = act
        self.rews_buf[self.ptr] = rew
        self.done_buf[self.ptr] = done
        self.ptr = (self.ptr+1) % self.max_size
        self.size = min(self.size+1, self.max_size)
    
      def sample_batch(self, batch_size=320):
        idxs = np.random.randint(0, self.size, size=batch_size)
        return dict(s=self.obs1_buf[idxs],
                    s2=self.obs2_buf[idxs],
                    a=self.acts_buf[idxs],
                    r=self.rews_buf[idxs],
                    d=self.done_buf[idxs])

class MultiStockEnv:
    """
      A 3-stock trading environment.
      State: vector of size 7 (n_stock * 2 + 1)
    - # shares of stock 1 owned
    - # shares of stock 2 owned
    - # shares of stock 3 owned
    - price of stock 1 (using daily close price)
    - price of stock 2
    - price of stock 3
    - cash owned (can be used to purchase more stocks)
      Action: categorical variable with 27 (3^3) possibilities
    - for each stock, you can:
    - 0 = sell
    - 1 = hold
    - 2 = buy
      """
    def __init__(self, data, initial_investment=20000):
        # data
        self.stock_price_history = data.iloc[:,-2:].values
        self.n_step, self.state_dim = data.shape
        
        # instance attributes
        self.initial_investment = initial_investment
        self.cur_step = None
        self.stock_owned = None
        self.stock_price = None
        self.cash_in_hand = None
        self.n_stock = 2
        
        self.action_space = np.arange(3**self.n_stock)
        
        # action permutations
        # returns a nested list with elements like:
        # [0,0,0]
        # [0,0,1]
        # [0,0,2]
        # [0,1,0]
        # [0,1,1]
        # etc.
        # 0 = sell
        # 1 = hold
        # 2 = buy
        self.action_list = list(map(list, itertools.product([0, 1, 2], repeat=self.n_stock)))
    
        # calculate size of state
        self.state_dim = self.state_dim + 3
        
        self.reset()
        
    def reset(self):
        self.cur_step = 0
        self.stock_owned = np.zeros(self.n_stock)
        self.stock_price = self.stock_price_history[self.cur_step]
        self.cash_in_hand = self.initial_investment
        return self._get_obs()

    def _get_obs(self):
        obs = np.empty(self.state_dim)
        obs[:self.n_stock] = self.stock_owned
        obs[self.n_stock:2*self.n_stock] = self.stock_price
        obs[4] = self.cash_in_hand
        obs[5:] = data.iloc[self.cur_step,:-2]
        return obs
    
    def _get_val(self):
        return self.stock_owned.dot(self.stock_price) + self.cash_in_hand
        
    def _trade(self, action):
      # index the action we want to perform
      # 0 = sell
      # 1 = hold
      # 2 = buy
      # e.g. [2,1,0] means:
      # buy first stock
      # hold second stock
      # sell third stock
      action_vec = self.action_list[action]
      
      # determine which stocks to buy or sell
      sell_index = [] # stores index of stocks we want to sell
      buy_index = [] # stores index of stocks we want to buy
      for i, a in enumerate(action_vec):
        if a == 0:
          sell_index.append(i)
        elif a == 2:
          buy_index.append(i)
      
      # sell any stocks we want to sellself.stock_owned[1]
      # then buy any stocks we want to buy
      if sell_index:
        # NOTE: to simplify the problem, when we sell, we will sell ALL shares of that stock
        for i in sell_index:
          self.cash_in_hand += self.stock_price[i] * self.stock_owned[i] * 50
          self.stock_owned[i] = 0
      if buy_index:
        # NOTE: when buying, we will loop through each stock we want to buy,
        #       and buy one share at a time until we run out of cash
        can_buy = True
        while can_buy:
          for i in buy_index:
            if self.cash_in_hand > self.stock_price[i] * 50:
              self.stock_owned[i] += 1 # buy one share
              self.cash_in_hand -= self.stock_price[i] * 50
            else:
              can_buy = False

    def step(self, action):
        assert action in self.action_space
    
        # get current value before performing the action
        prev_val = self._get_val()
    
        # update price, i.e. go to the next day
        self.cur_step += 1

        self.stock_price = self.stock_price_history[self.cur_step]
    
        # perform the trade
        self._trade(action)
    
        # get the new value after taking the action
        cur_val = self._get_val()
    
        # reward is the increase in porfolio value
        reward = cur_val - prev_val
    
        # done if we have run out of data
        done = self.cur_step == self.n_step - 1
    
        # store the current value of the portfolio here
        info = {'cur_val': cur_val}
    
        # conform to the Gym API
        return self._get_obs(), reward, done, info

class DQNAgent(object):
    def __init__(self, state_size, action_size):
      self.state_size = state_size
      self.action_size = action_size
      self.memory = ReplayBuffer(state_size, action_size, size=64)
      self.gamma = 0.95  # discount rate
      # self.epsilon = 1000 # exploration rate
      self.epsilon_min = 0.1
      self.epsilon_decay = 0.995
      self.model = mlp(state_size, action_size)
      self.random_trades = 0

    def update_replay_memory(self, state, action, reward, next_state, done):
        self.memory.store(state, action, reward, next_state, done)
    
    
    def act(self, state):
      if np.random.rand() <= self.epsilon:
          self.random_trades +=1
          return np.random.choice(self.action_size)
      act_values = self.model.predict(state)
      return np.argmax(act_values[0])  # returns action
    
    
    def replay(self, batch_size=32):
      # first check if replay buffer contains enough data
      if self.memory.size < batch_size:
          return
    
      # sample a batch of data from the replay memory
      minibatch = self.memory.sample_batch(batch_size)
      states = minibatch['s']
      actions = minibatch['a']
      rewards = minibatch['r']
      next_states = minibatch['s2']
      done = minibatch['d']
    
      # Calculate the tentative target: Q(s',a)
      target = rewards + (1 - done) * self.gamma * np.amax(self.model.predict(next_states), axis=1)
    
      # With the Keras API, the target (usually) must have the same
      # shape as the predictions.
      # However, we only need to update the network for the actions
      # which were actually taken.
      # We can accomplish this by setting the target to be equal to
      # the prediction for all values.
      # Then, only change the targets for the actions taken.
      # Q(s,a)
      target_full = self.model.predict(states)
      target_full[np.arange(batch_size), actions] = target
    
      # Run one training step
      self.model.train_on_batch(states, target_full)
    
      if self.epsilon > self.epsilon_min:
          self.epsilon *= self.epsilon_decay
    
    
    def load(self, name):
        self.model.load_weights(name)
    
    
    def save(self, name):
        self.model.save_weights(name)

def play_one_episode(agent, env):
    # note: after transforming states are already 1xD
    state = env.reset()
    state = scaler.transform([state])
    done = False
    agent.random_trades = 0
    k=0
      
    while not done:
        action = agent.act(state)
        next_state, reward, done, info = env.step(action)
        # print(f'{k}) action = {action}, reward = {reward}')
        # if float(info['cur_val']) < 20000:
        #     continue
        # else:
        next_state = scaler.transform([next_state])
        agent.update_replay_memory(state, action, reward, next_state, done)
        agent.replay(batch_size)
        state = next_state
        k +=1 


    
    return info['cur_val'] #if info['cur_val'] > 20000 else 0

if __name__ == '__main__':

    # config
    models_folder = './rl_trader_models_MACD_only' #where models and scaler are saved
    rewards_folder = './rl_trader_rewards_MACD_olnly' #where results are saved
    num_episodes = 100 #number of loops per a cycle

    initial_investment = 2000


    maybe_make_dir(models_folder)
    maybe_make_dir(rewards_folder)

    res=get_data()
    while True:
        succeded_trades = 0 # To count percentage of success
        try:
            ib = IB()
            ib.connect('127.0.0.1', 7497, clientId=np.random.randint(10, 1000))
            ES = Future(symbol='ES', lastTradeDateOrContractMonth='20200918', exchange='GLOBEX',
                        currency='USD')
            ib.qualifyContracts(ES)
            endDateTime = ''
            No_days = '3 D'
            interval = '1 min'
            data = res.options(res.options(res.ES(),res.option_history(res.get_contract('C', 2000)))\
                               ,res.option_history(res.get_contract('P', 2000))) #collect live data of ES with TA and options prices
            data.to_csv('./new_data.csv') # save data incase tws goes dowen
        except:
            data_raw=pd.read_csv('./new_data.csv',index_col='date')
        data = data_raw[['macd', 'macdsignal', 'macdhist', 'ES_C_close', 'ES_P_close']] #choose parameters to drop if not needed
        n_timesteps = len(data)
        n_stocks = 2
        n_train = int(n_timesteps)
        

        train_data = data
        batch_size = 32 
        env = MultiStockEnv(train_data, initial_investment) # start envirnoment
        state_size = env.state_dim
        action_size = len(env.action_space)
        agent = DQNAgent(state_size, action_size)
        scaler = get_scaler(env)
        agent.epsilon = 100
        try:
            agent.load(f'{models_folder}/dqn.h5') # load agent
        except Exception as error:
            print(error)
        try:
            with open(f'{rewards_folder}/scaler.pickle', 'rb') as f:
                scaler = pickle.load(f)  # load scaler  # load scaler
        except Exception as error:
            print(error)
        # store the final value of the portfolio (end of episode)
        portfolio_value = []
        # agent.epsilon = 1  # exploration rate
        # agent.epsilon_min = 0.01
        # agent.epsilon_decay = 0.995
        # play the game num_episodes times
        for e in range(num_episodes):
            t0 = datetime.now()
            val = play_one_episode(agent, env)
            if val >20000: # take only profitable trades
                succeded_trades +=1
            print(f'Number of random trades = {agent.random_trades} from {len(train_data)} or {round(100*agent.random_trades/len(train_data),0)}% and Epsilon = {agent.epsilon}' )
            dt = datetime.now() - t0
            print(f"episode: {e + 1}/{num_episodes}, episode end value: {val:.2f}, duration: {dt}")
            portfolio_value.append(val) # append episode end portfolio value
            if agent.epsilon >= agent.epsilon_min:
                agent.epsilon *=agent.epsilon_decay

        initial_investment = val
        print(f'*****Loop finished, No. of succeded trades = {succeded_trades}, percentage = {succeded_trades/num_episodes*100}%')
        agent.save(f'{models_folder}/dqn.h5')
        
        
        f = open("/home/alex/Projects/RL_trade_ES_futures/rl_trader_rewards/scaler.pickle","wb")
        pickle.dump(scaler, f)
        f.close()
        # save the scaler
        # with open(f'{models_folder}/scaler.pkl', 'wb') as f:
        #   pickle.dump(scaler, f)

        # save portfolio value for each episode

        np.save(f'{rewards_folder}/reward.npy', np.array(portfolio_value))
        np.save(f'{rewards_folder}/succeded_trades.npy', np.array(succeded_trades))
        np.save(f'{rewards_folder}/succeded_trades.npy', np.array(agent.random_trades))




        # if args == 'test':
        #   # then load the previous scaler
        #   with open(f'{models_folder}/scaler.pkl', 'rb') as f:
        #     scaler = pickle.load(f)

        #   # remake the env with test data
        #   env = MultiStockEnv(test_data, initial_investment)

        #   # make sure epsilon is not 1!
        #   # no need to run multiple episodes if epsilon = 0, it's deterministic
        #   agent.epsilon = 0.01

        #   # load trained weights
        #   agent.load(f'{models_folder}/dqn.h5')
