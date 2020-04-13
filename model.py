import torch
import torch.nn as nn
import math
import torch.nn.functional as F
import config
import copy

############# We will break the model into 6 Subparts #############
## 1. Embedding Class 
## 2. Attention Class
## 3. Feed Forward Class
## 4. Encoder Class
## 5. Decoder Class
## 6. Transformer Class


## Select the device
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

def get_clones(module, N):
    return nn.ModuleList([copy.deepcopy(module) for i in range(N)])


def scaled_dot_product_attention(query, key, value, mask):
    '''query, key, value : batch_size * heads * max_len * d_h
        return output : batch_size * heads * max_len * d_h
    '''
    
    matmul = torch.matmul(query,key.transpose(-2,-1))

    scale = torch.tensor(query.shape[-1],dtype=float)

    logits = matmul / torch.sqrt(scale)

    if mask is not None:
        logits += (mask * -1e9)
    
    attention_weights = F.softmax(logits,dim = -1)

    output = torch.matmul(attention_weights,value)

    return output


class MultiHeadAttention(nn.Module):

    def __init__(self, d_model, num_heads):
        super().__init__()
        self.h = num_heads
        self.d_model = d_model

        assert d_model % self.num_heads == 0

        self.d_h = d_model // self.num_heads

        self.q_dense = nn.Linear(d_model,d_model)
        self.k_dense = nn.Linear(d_model,d_model)
        self.v_dense = nn.Linear(d_model,d_model)

        self.out = nn.Linear(d_model,d_model)

    
    def forward(self, q, k, v, mask = None):
        
        # batch_size
        bs = q.size(0)

        k = self.k_dense(k).view(bs, -1, self.h, self.d_h)
        q = self.q_dense(q).view(bs, -1, self.h, self.d_h)
        v = self.v_dense(v).view(bs, -1, self.h, self.d_h)

        k = k.transpose(1,2)
        q = q.transpose(1,2)
        v = v.transpose(1,2)

        scores = scaled_dot_product_attention(q,k,v,mask)
        
        # concat each heads
        concat = scores.transpose(1,2).contiguous()\
            .view(bs,-1,self.d_model)
        
        out = self.out(concat)

        return out



def create_padding_mask(x):

    mask = (x == 0) * 1
    mask = mask.unsqueeze(1).unsqueeze(1)
    return mask


def create_look_ahead_mask(x):

    seq_len = x.shape[1]
    mask = torch.triu(torch.ones(seq_len, seq_len)).transpose(0, 1).type(dtype=torch.uint8)
    mask = mask.to(device)
    mask = (mask == 0) * 1
    mask = mask.unsqueeze(0)
    pad_mask = create_padding_mask(x)
    return torch.max(mask,pad_mask)


class PositionalEncoding(nn.Module):

    def __init__(self, d_model, dropout=0.1, max_len=50):
        super(PositionalEncoding, self).__init__()
        self.dropout = nn.Dropout(p=dropout)
        self.d_model = d_model

        pe = torch.zeros(max_len, d_model).to(device)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        pe = pe.unsqueeze(0)
        self.pe = pe

    def forward(self, x):
        x *= torch.sqrt(self.d_model)
        x +=  self.pe
        return self.dropout(x)


class FeedForward(nn.Module):
    def __init__(self,d_model,d_ff = 2048,dropout = 0.1):
        super().__init__()
        self.linear_1 = nn.Linear(d_model,d_ff)
        self.dropout = nn.Dropout(dropout)
        self.linear2 = nn.Linear(d_ff, d_model)

    def forward(self,x):
        x = F.relu(self.linear_1(x))
        x = self.dropout(x)
        x = self.linear2(x)
        return x

class Embedder(nn.Module):
    def __init__(self,vocab_size,d_model):
        super().__init__()
        self.embed = nn.Embedding(vocab_size, d_model)
    
    def forward(self,x):
        return self.embed(x)


class EncoderLayer(nn.Module):
    def __init__(self, d_model, heads, dropout = 0.1):
        super().__init__()
        self.layernorm1 = nn.LayerNorm(d_model)
        self.layernorm2 = nn.LayerNorm(d_model)
        self.attn = MultiHeadAttention(heads, d_model)
        self.ff = FeedForward(d_model)
        self.dropout_1 = nn.Dropout(dropout)
        self.dropout_2 = nn.Dropout(dropout)

    
    def forward(self,x,mask):
        x1 = self.norm_1(x)
        x1 = x + self.dropout_1(self.attn(x1,
                                x1,x1,mask))
        x2 = self.norm_2(x1)
        x3 = x1 + self.dropout_2(self.ff(x2))

        return x3


class DecoderLayer(nn.Module):
    def __init__(self, d_model, heads, dropout = 0.1):
        super().__init__()
        self.norm1 = nn.LayerNorm(d_model)
        self.norm2 = nn.LayerNorm(d_model)
        self.norm3 = nn.LayerNorm(d_model)

        self.dropout_1 = nn.Dropout(dropout)
        self.dropout_2 = nn.Dropout(dropout)
        self.dropout_3 = nn.Dropout(dropout)

        self.attn_1 = MultiHeadAttention(heads,d_model)
        self.attn_2 = MultiHeadAttention(heads, d_model)
        self.ff = FeedForward(d_model)

    def forward(self, x, encoder_out, src_mask, trg_mask):

        x2 = self.norm_1(x)
        x = x + self.dropout_1(self.attn_1(x2, x2, x2, trg_mask))
        x2 = self.norm_2(x)
        x = x + self.dropout_2(self.attn_2(x2, encoder_out, encoder_out,
        src_mask))
        x2 = self.norm_3(x)
        x = x + self.dropout_3(self.ff(x2))
        return x


class Encoder(nn.Module):
    def __init__(self, vocab_size, d_model, N, heads):
        super().__init__()
        self.N = N
        self.embed = Embedder(vocab_size, d_model)
        self.pe = PositionalEncoding(d_model)
        self.layers = get_clones(EncoderLayer(d_model, heads), N)
        self.norm = nn.LayerNorm(d_model)

    def forward(self, src, mask):
        x = self.embed(src)
        x = self.pe(x)
        for i in range(N):
            x = self.layers[i](x, mask)
        return self.norm(x)
    

class Decoder(nn.Module):
    def __init__(self, vocab_size, d_model, N, heads):
        super().__init__()
        self.N = N
        self.embed = Embedder(vocab_size, d_model)
        self.pe = PositionalEncoding(d_model)
        self.layers = get_clones(DecoderLayer(d_model, heads), N)
        self.norm = nn.LayerNorm(d_model)

    def forward(self, trg, e_outputs, src_mask, trg_mask):
        x = self.embed(trg)
        x = self.pe(x)
        for i in range(self.N):
            x = self.layers[i](x, e_outputs, src_mask, trg_mask)
        return self.norm(x)

    
class Transformer(nn.Module):
    def __init__(self, vocab_size, d_model, num_layers, heads):
        super().__init__()
        self.encoder = Encoder(vocab_size,d_model,num_layers,heads)
        self.decoder = Decoder(vocab_size,d_model,num_layers,heads)
        self.out = nn.Linear(d_model, vocab_size)
    
    def forward(self, src, trg, src_mask, trg_mask):

        e_outputs = self.encoder(src, src_mask)
        d_output = self.decoder(trg, e_outputs, src_mask, trg_mask)
        output = self.out(d_output)
        return output




query = np.array([[[3,4],[3,4],[4,3]]], dtype = float)
key = np.array([[[3,4],[3,4],[4,3]]], dtype = float)
value = np.array([[[3,4],[3,4],[4,3]]], dtype = float)
query = torch.from_numpy(query)
key = torch.from_numpy(key)
value = torch.from_numpy(value)
print(query.shape)
mask = None

scaled_dot_product_attention(query,key,value,mask)

    