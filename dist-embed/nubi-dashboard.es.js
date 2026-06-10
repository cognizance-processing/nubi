function O(n, t, e, i) {
  function s(r) {
    return r instanceof e ? r : new e(function(o) {
      o(r);
    });
  }
  return new (e || (e = Promise))(function(r, o) {
    function a(d) {
      try {
        u(i.next(d));
      } catch (h) {
        o(h);
      }
    }
    function c(d) {
      try {
        u(i.throw(d));
      } catch (h) {
        o(h);
      }
    }
    function u(d) {
      d.done ? r(d.value) : s(d.value).then(a, c);
    }
    u((i = i.apply(n, t || [])).next());
  });
}
function ds(n) {
  var t = typeof Symbol == "function" && Symbol.iterator, e = t && n[t], i = 0;
  if (e) return e.call(n);
  if (n && typeof n.length == "number") return {
    next: function() {
      return n && i >= n.length && (n = void 0), { value: n && n[i++], done: !n };
    }
  };
  throw new TypeError(t ? "Object is not iterable." : "Symbol.iterator is not defined.");
}
function F(n) {
  return this instanceof F ? (this.v = n, this) : new F(n);
}
function Nt(n, t, e) {
  if (!Symbol.asyncIterator) throw new TypeError("Symbol.asyncIterator is not defined.");
  var i = e.apply(n, t || []), s, r = [];
  return s = Object.create((typeof AsyncIterator == "function" ? AsyncIterator : Object).prototype), a("next"), a("throw"), a("return", o), s[Symbol.asyncIterator] = function() {
    return this;
  }, s;
  function o(B) {
    return function(z) {
      return Promise.resolve(z).then(B, h);
    };
  }
  function a(B, z) {
    i[B] && (s[B] = function(wt) {
      return new Promise(function(re, ct) {
        r.push([B, wt, re, ct]) > 1 || c(B, wt);
      });
    }, z && (s[B] = z(s[B])));
  }
  function c(B, z) {
    try {
      u(i[B](z));
    } catch (wt) {
      N(r[0][3], wt);
    }
  }
  function u(B) {
    B.value instanceof F ? Promise.resolve(B.value.v).then(d, h) : N(r[0][2], B);
  }
  function d(B) {
    c("next", B);
  }
  function h(B) {
    c("throw", B);
  }
  function N(B, z) {
    B(z), r.shift(), r.length && c(r[0][0], r[0][1]);
  }
}
function gn(n) {
  var t, e;
  return t = {}, i("next"), i("throw", function(s) {
    throw s;
  }), i("return"), t[Symbol.iterator] = function() {
    return this;
  }, t;
  function i(s, r) {
    t[s] = n[s] ? function(o) {
      return (e = !e) ? { value: F(n[s](o)), done: !1 } : r ? r(o) : o;
    } : r;
  }
}
function _e(n) {
  if (!Symbol.asyncIterator) throw new TypeError("Symbol.asyncIterator is not defined.");
  var t = n[Symbol.asyncIterator], e;
  return t ? t.call(n) : (n = typeof ds == "function" ? ds(n) : n[Symbol.iterator](), e = {}, i("next"), i("throw"), i("return"), e[Symbol.asyncIterator] = function() {
    return this;
  }, e);
  function i(r) {
    e[r] = n[r] && function(o) {
      return new Promise(function(a, c) {
        o = n[r](o), s(a, c, o.done, o.value);
      });
    };
  }
  function s(r, o, a, c) {
    Promise.resolve(c).then(function(u) {
      r({ value: u, done: a });
    }, o);
  }
}
const hs = new TextDecoder("utf-8"), ui = hs.decode.bind(hs), wa = new TextEncoder(), sn = (n) => wa.encode(n), Ia = (n) => typeof n == "number", Sa = (n) => typeof n == "boolean", G = (n) => typeof n == "function", _t = (n) => n != null && Object(n) === n, qe = (n) => _t(n) && G(n.then), qn = (n) => _t(n) && G(n[Symbol.iterator]), Bi = (n) => _t(n) && G(n[Symbol.asyncIterator]), di = (n) => _t(n) && _t(n.schema), $s = (n) => _t(n) && "done" in n && "value" in n, Ys = (n) => _t(n) && G(n.stat) && Ia(n.fd), Hs = (n) => _t(n) && Ai(n.body), Ws = (n) => "_getDOMStream" in n && "_getNodeStream" in n, Ai = (n) => _t(n) && G(n.cancel) && G(n.getReader) && !Ws(n), qs = (n) => _t(n) && G(n.read) && G(n.pipe) && Sa(n.readable) && !Ws(n), Ba = (n) => _t(n) && G(n.clear) && G(n.bytes) && G(n.position) && G(n.setPosition) && G(n.capacity) && G(n.getBufferIdentifier) && G(n.createLong), Di = typeof SharedArrayBuffer < "u" ? SharedArrayBuffer : ArrayBuffer;
function Aa(n) {
  const t = n[0] ? [n[0]] : [];
  let e, i, s, r;
  for (let o, a, c = 0, u = 0, d = n.length; ++c < d; ) {
    if (o = t[u], a = n[c], !o || !a || o.buffer !== a.buffer || a.byteOffset < o.byteOffset) {
      a && (t[++u] = a);
      continue;
    }
    if ({ byteOffset: e, byteLength: s } = o, { byteOffset: i, byteLength: r } = a, e + s < i || i + r < e) {
      a && (t[++u] = a);
      continue;
    }
    t[u] = new Uint8Array(o.buffer, e, i - e + r);
  }
  return t;
}
function hi(n, t, e = 0, i = t.byteLength) {
  const s = n.byteLength, r = new Uint8Array(n.buffer, n.byteOffset, s), o = new Uint8Array(t.buffer, t.byteOffset, Math.min(i, s));
  return r.set(o, e), n;
}
function Tt(n, t) {
  const e = Aa(n), i = e.reduce((d, h) => d + h.byteLength, 0);
  let s, r, o, a = 0, c = -1;
  const u = Math.min(t || Number.POSITIVE_INFINITY, i);
  for (const d = e.length; ++c < d; ) {
    if (s = e[c], r = s.subarray(0, Math.min(s.length, u - a)), u <= a + r.length) {
      r.length < s.length ? e[c] = s.subarray(r.length) : r.length === s.length && c++, o ? hi(o, r, a) : o = r;
      break;
    }
    hi(o || (o = new Uint8Array(u)), r, a), a += r.length;
  }
  return [o || new Uint8Array(0), e.slice(c), i - (o ? o.byteLength : 0)];
}
function R(n, t) {
  let e = $s(t) ? t.value : t;
  return e instanceof n ? n === Uint8Array ? new n(e.buffer, e.byteOffset, e.byteLength) : e : e ? (typeof e == "string" && (e = sn(e)), e instanceof ArrayBuffer ? new n(e) : e instanceof Di ? new n(e) : Ba(e) ? R(n, e.bytes()) : ArrayBuffer.isView(e) ? e.byteLength <= 0 ? new n(0) : new n(e.buffer, e.byteOffset, e.byteLength / n.BYTES_PER_ELEMENT) : n.from(e)) : new n(0);
}
const Ve = (n) => R(Int32Array, n), fs = (n) => R(BigInt64Array, n), T = (n) => R(Uint8Array, n), fi = (n) => (n.next(), n);
function* Da(n, t) {
  const e = function* (s) {
    yield s;
  }, i = typeof t == "string" || ArrayBuffer.isView(t) || t instanceof ArrayBuffer || t instanceof Di ? e(t) : qn(t) ? t : e(t);
  return yield* fi((function* (s) {
    let r = null;
    do
      r = s.next(yield R(n, r));
    while (!r.done);
  })(i[Symbol.iterator]())), new n();
}
const Oa = (n) => Da(Uint8Array, n);
function Js(n, t) {
  return Nt(this, arguments, function* () {
    if (qe(t))
      return yield F(yield F(yield* gn(_e(Js(n, yield F(t))))));
    const i = function(o) {
      return Nt(this, arguments, function* () {
        yield yield F(yield F(o));
      });
    }, s = function(o) {
      return Nt(this, arguments, function* () {
        yield F(yield* gn(_e(fi((function* (a) {
          let c = null;
          do
            c = a.next(yield c?.value);
          while (!c.done);
        })(o[Symbol.iterator]())))));
      });
    }, r = typeof t == "string" || ArrayBuffer.isView(t) || t instanceof ArrayBuffer || t instanceof Di ? i(t) : qn(t) ? s(t) : Bi(t) ? t : i(t);
    return yield F(
      // otherwise if AsyncIterable, use it
      yield* gn(_e(fi((function(o) {
        return Nt(this, arguments, function* () {
          let a = null;
          do
            a = yield F(o.next(yield yield F(R(n, a))));
          while (!a.done);
        });
      })(r[Symbol.asyncIterator]()))))
    ), yield F(new n());
  });
}
const Fa = (n) => Js(Uint8Array, n);
function Ma(n, t) {
  let e = 0;
  const i = n.length;
  if (i !== t.length)
    return !1;
  if (i > 0)
    do
      if (n[e] !== t[e])
        return !1;
    while (++e < i);
  return !0;
}
const ut = {
  fromIterable(n) {
    return fn(Na(n));
  },
  fromAsyncIterable(n) {
    return fn(Ta(n));
  },
  fromDOMStream(n) {
    return fn(La(n));
  },
  fromNodeStream(n) {
    return fn(Ua(n));
  },
  // @ts-ignore
  toDOMStream(n, t) {
    throw new Error('"toDOMStream" not available in this environment');
  },
  // @ts-ignore
  toNodeStream(n, t) {
    throw new Error('"toNodeStream" not available in this environment');
  }
}, fn = (n) => (n.next(), n);
function* Na(n) {
  let t, e = !1, i = [], s, r, o, a = 0;
  function c() {
    return r === "peek" ? Tt(i, o)[0] : ([s, i, a] = Tt(i, o), s);
  }
  ({ cmd: r, size: o } = (yield null) || { cmd: "read", size: 0 });
  const u = Oa(n)[Symbol.iterator]();
  try {
    do
      if ({ done: t, value: s } = Number.isNaN(o - a) ? u.next() : u.next(o - a), !t && s.byteLength > 0 && (i.push(s), a += s.byteLength), t || o <= a)
        do
          ({ cmd: r, size: o } = yield c());
        while (o < a);
    while (!t);
  } catch (d) {
    e = !0, typeof u.throw == "function" && u.throw(d);
  } finally {
    e === !1 && typeof u.return == "function" && u.return(null);
  }
  return null;
}
function Ta(n) {
  return Nt(this, arguments, function* () {
    let e, i = !1, s = [], r, o, a, c = 0;
    function u() {
      return o === "peek" ? Tt(s, a)[0] : ([r, s, c] = Tt(s, a), r);
    }
    ({ cmd: o, size: a } = (yield yield F(null)) || { cmd: "read", size: 0 });
    const d = Fa(n)[Symbol.asyncIterator]();
    try {
      do
        if ({ done: e, value: r } = Number.isNaN(a - c) ? yield F(d.next()) : yield F(d.next(a - c)), !e && r.byteLength > 0 && (s.push(r), c += r.byteLength), e || a <= c)
          do
            ({ cmd: o, size: a } = yield yield F(u()));
          while (a < c);
      while (!e);
    } catch (h) {
      i = !0, typeof d.throw == "function" && (yield F(d.throw(h)));
    } finally {
      i === !1 && typeof d.return == "function" && (yield F(d.return(new Uint8Array(0))));
    }
    return yield F(null);
  });
}
function La(n) {
  return Nt(this, arguments, function* () {
    let e = !1, i = !1, s = [], r, o, a, c = 0;
    function u() {
      return o === "peek" ? Tt(s, a)[0] : ([r, s, c] = Tt(s, a), r);
    }
    ({ cmd: o, size: a } = (yield yield F(null)) || { cmd: "read", size: 0 });
    const d = new xa(n);
    try {
      do
        if ({ done: e, value: r } = Number.isNaN(a - c) ? yield F(d.read()) : yield F(d.read(a - c)), !e && r.byteLength > 0 && (s.push(T(r)), c += r.byteLength), e || a <= c)
          do
            ({ cmd: o, size: a } = yield yield F(u()));
          while (a < c);
      while (!e);
    } catch (h) {
      i = !0, yield F(d.cancel(h));
    } finally {
      i === !1 ? yield F(d.cancel()) : n.locked && d.releaseLock();
    }
    return yield F(null);
  });
}
class xa {
  constructor(t) {
    this.source = t, this.reader = null, this.reader = this.source.getReader(), this.reader.closed.catch(() => {
    });
  }
  get closed() {
    return this.reader ? this.reader.closed.catch(() => {
    }) : Promise.resolve();
  }
  releaseLock() {
    this.reader && this.reader.releaseLock(), this.reader = null;
  }
  cancel(t) {
    return O(this, void 0, void 0, function* () {
      const { reader: e, source: i } = this;
      e && (yield e.cancel(t).catch(() => {
      })), i && i.locked && this.releaseLock();
    });
  }
  read(t) {
    return O(this, void 0, void 0, function* () {
      if (t === 0)
        return { done: this.reader == null, value: new Uint8Array(0) };
      const e = yield this.reader.read();
      return !e.done && (e.value = T(e)), e;
    });
  }
}
const ni = (n, t) => {
  const e = (s) => i([t, s]);
  let i;
  return [t, e, new Promise((s) => (i = s) && n.once(t, e))];
};
function Ua(n) {
  return Nt(this, arguments, function* () {
    const e = [];
    let i = "error", s = !1, r = null, o, a, c = 0, u = [], d;
    function h() {
      return o === "peek" ? Tt(u, a)[0] : ([d, u, c] = Tt(u, a), d);
    }
    if ({ cmd: o, size: a } = (yield yield F(null)) || { cmd: "read", size: 0 }, n.isTTY)
      return yield yield F(new Uint8Array(0)), yield F(null);
    try {
      e[0] = ni(n, "end"), e[1] = ni(n, "error");
      do {
        if (e[2] = ni(n, "readable"), [i, r] = yield F(Promise.race(e.map((B) => B[2]))), i === "error")
          break;
        if ((s = i === "end") || (Number.isFinite(a - c) ? (d = T(n.read(a - c)), d.byteLength < a - c && (d = T(n.read()))) : d = T(n.read()), d.byteLength > 0 && (u.push(d), c += d.byteLength)), s || a <= c)
          do
            ({ cmd: o, size: a } = yield yield F(h()));
          while (a < c);
      } while (!s);
    } finally {
      yield F(N(e, i === "error" ? r : null));
    }
    return yield F(null);
    function N(B, z) {
      return d = u = null, new Promise((wt, re) => {
        for (const [ct, hn] of B)
          n.off(ct, hn);
        try {
          const ct = n.destroy;
          ct && ct.call(n, z), z = void 0;
        } catch (ct) {
          z = ct || z;
        } finally {
          z != null ? re(z) : wt();
        }
      });
    }
  });
}
var $;
(function(n) {
  n[n.V1 = 0] = "V1", n[n.V2 = 1] = "V2", n[n.V3 = 2] = "V3", n[n.V4 = 3] = "V4", n[n.V5 = 4] = "V5";
})($ || ($ = {}));
var tt;
(function(n) {
  n[n.Sparse = 0] = "Sparse", n[n.Dense = 1] = "Dense";
})(tt || (tt = {}));
var W;
(function(n) {
  n[n.HALF = 0] = "HALF", n[n.SINGLE = 1] = "SINGLE", n[n.DOUBLE = 2] = "DOUBLE";
})(W || (W = {}));
var ft;
(function(n) {
  n[n.DAY = 0] = "DAY", n[n.MILLISECOND = 1] = "MILLISECOND";
})(ft || (ft = {}));
var b;
(function(n) {
  n[n.SECOND = 0] = "SECOND", n[n.MILLISECOND = 1] = "MILLISECOND", n[n.MICROSECOND = 2] = "MICROSECOND", n[n.NANOSECOND = 3] = "NANOSECOND";
})(b || (b = {}));
var J;
(function(n) {
  n[n.YEAR_MONTH = 0] = "YEAR_MONTH", n[n.DAY_TIME = 1] = "DAY_TIME", n[n.MONTH_DAY_NANO = 2] = "MONTH_DAY_NANO";
})(J || (J = {}));
const ii = 2, Ot = 4, Ct = 4, E = 4, qt = new Int32Array(2), ps = new Float32Array(qt.buffer), ys = new Float64Array(qt.buffer), pn = new Uint16Array(new Uint8Array([1, 0]).buffer)[0] === 1;
var pi;
(function(n) {
  n[n.UTF8_BYTES = 1] = "UTF8_BYTES", n[n.UTF16_STRING = 2] = "UTF16_STRING";
})(pi || (pi = {}));
let ee = class Ks {
  /**
   * Create a new ByteBuffer with a given array of bytes (`Uint8Array`)
   */
  constructor(t) {
    this.bytes_ = t, this.position_ = 0, this.text_decoder_ = new TextDecoder();
  }
  /**
   * Create and allocate a new ByteBuffer with a given size.
   */
  static allocate(t) {
    return new Ks(new Uint8Array(t));
  }
  clear() {
    this.position_ = 0;
  }
  /**
   * Get the underlying `Uint8Array`.
   */
  bytes() {
    return this.bytes_;
  }
  /**
   * Get the buffer's position.
   */
  position() {
    return this.position_;
  }
  /**
   * Set the buffer's position.
   */
  setPosition(t) {
    this.position_ = t;
  }
  /**
   * Get the buffer's capacity.
   */
  capacity() {
    return this.bytes_.length;
  }
  readInt8(t) {
    return this.readUint8(t) << 24 >> 24;
  }
  readUint8(t) {
    return this.bytes_[t];
  }
  readInt16(t) {
    return this.readUint16(t) << 16 >> 16;
  }
  readUint16(t) {
    return this.bytes_[t] | this.bytes_[t + 1] << 8;
  }
  readInt32(t) {
    return this.bytes_[t] | this.bytes_[t + 1] << 8 | this.bytes_[t + 2] << 16 | this.bytes_[t + 3] << 24;
  }
  readUint32(t) {
    return this.readInt32(t) >>> 0;
  }
  readInt64(t) {
    return BigInt.asIntN(64, BigInt(this.readUint32(t)) + (BigInt(this.readUint32(t + 4)) << BigInt(32)));
  }
  readUint64(t) {
    return BigInt.asUintN(64, BigInt(this.readUint32(t)) + (BigInt(this.readUint32(t + 4)) << BigInt(32)));
  }
  readFloat32(t) {
    return qt[0] = this.readInt32(t), ps[0];
  }
  readFloat64(t) {
    return qt[pn ? 0 : 1] = this.readInt32(t), qt[pn ? 1 : 0] = this.readInt32(t + 4), ys[0];
  }
  writeInt8(t, e) {
    this.bytes_[t] = e;
  }
  writeUint8(t, e) {
    this.bytes_[t] = e;
  }
  writeInt16(t, e) {
    this.bytes_[t] = e, this.bytes_[t + 1] = e >> 8;
  }
  writeUint16(t, e) {
    this.bytes_[t] = e, this.bytes_[t + 1] = e >> 8;
  }
  writeInt32(t, e) {
    this.bytes_[t] = e, this.bytes_[t + 1] = e >> 8, this.bytes_[t + 2] = e >> 16, this.bytes_[t + 3] = e >> 24;
  }
  writeUint32(t, e) {
    this.bytes_[t] = e, this.bytes_[t + 1] = e >> 8, this.bytes_[t + 2] = e >> 16, this.bytes_[t + 3] = e >> 24;
  }
  writeInt64(t, e) {
    this.writeInt32(t, Number(BigInt.asIntN(32, e))), this.writeInt32(t + 4, Number(BigInt.asIntN(32, e >> BigInt(32))));
  }
  writeUint64(t, e) {
    this.writeUint32(t, Number(BigInt.asUintN(32, e))), this.writeUint32(t + 4, Number(BigInt.asUintN(32, e >> BigInt(32))));
  }
  writeFloat32(t, e) {
    ps[0] = e, this.writeInt32(t, qt[0]);
  }
  writeFloat64(t, e) {
    ys[0] = e, this.writeInt32(t, qt[pn ? 0 : 1]), this.writeInt32(t + 4, qt[pn ? 1 : 0]);
  }
  /**
   * Return the file identifier.   Behavior is undefined for FlatBuffers whose
   * schema does not include a file_identifier (likely points at padding or the
   * start of a the root vtable).
   */
  getBufferIdentifier() {
    if (this.bytes_.length < this.position_ + Ot + Ct)
      throw new Error("FlatBuffers: ByteBuffer is too short to contain an identifier.");
    let t = "";
    for (let e = 0; e < Ct; e++)
      t += String.fromCharCode(this.readInt8(this.position_ + Ot + e));
    return t;
  }
  /**
   * Look up a field in the vtable, return an offset into the object, or 0 if the
   * field is not present.
   */
  __offset(t, e) {
    const i = t - this.readInt32(t);
    return e < this.readInt16(i) ? this.readInt16(i + e) : 0;
  }
  /**
   * Initialize any Table-derived type to point to the union at the given offset.
   */
  __union(t, e) {
    return t.bb_pos = e + this.readInt32(e), t.bb = this, t;
  }
  /**
   * Create a JavaScript string from UTF-8 data stored inside the FlatBuffer.
   * This allocates a new string and converts to wide chars upon each access.
   *
   * To avoid the conversion to string, pass Encoding.UTF8_BYTES as the
   * "optionalEncoding" argument. This is useful for avoiding conversion when
   * the data will just be packaged back up in another FlatBuffer later on.
   *
   * @param offset
   * @param opt_encoding Defaults to UTF16_STRING
   */
  __string(t, e) {
    t += this.readInt32(t);
    const i = this.readInt32(t);
    t += Ot;
    const s = this.bytes_.subarray(t, t + i);
    return e === pi.UTF8_BYTES ? s : this.text_decoder_.decode(s);
  }
  /**
   * Handle unions that can contain string as its member, if a Table-derived type then initialize it,
   * if a string then return a new one
   *
   * WARNING: strings are immutable in JS so we can't change the string that the user gave us, this
   * makes the behaviour of __union_with_string different compared to __union
   */
  __union_with_string(t, e) {
    return typeof t == "string" ? this.__string(e) : this.__union(t, e);
  }
  /**
   * Retrieve the relative offset stored at "offset"
   */
  __indirect(t) {
    return t + this.readInt32(t);
  }
  /**
   * Get the start of data of a vector whose offset is stored at "offset" in this object.
   */
  __vector(t) {
    return t + this.readInt32(t) + Ot;
  }
  /**
   * Get the length of a vector whose offset is stored at "offset" in this object.
   */
  __vector_len(t) {
    return this.readInt32(t + this.readInt32(t));
  }
  __has_identifier(t) {
    if (t.length != Ct)
      throw new Error("FlatBuffers: file identifier must be length " + Ct);
    for (let e = 0; e < Ct; e++)
      if (t.charCodeAt(e) != this.readInt8(this.position() + Ot + e))
        return !1;
    return !0;
  }
  /**
   * A helper function for generating list for obj api
   */
  createScalarList(t, e) {
    const i = [];
    for (let s = 0; s < e; ++s) {
      const r = t(s);
      r !== null && i.push(r);
    }
    return i;
  }
  /**
   * A helper function for generating list for obj api
   * @param listAccessor function that accepts an index and return data at that index
   * @param listLength listLength
   * @param res result list
   */
  createObjList(t, e) {
    const i = [];
    for (let s = 0; s < e; ++s) {
      const r = t(s);
      r !== null && i.push(r.unpack());
    }
    return i;
  }
}, Gs = class Zs {
  /**
   * Create a FlatBufferBuilder.
   */
  constructor(t) {
    this.minalign = 1, this.vtable = null, this.vtable_in_use = 0, this.isNested = !1, this.object_start = 0, this.vtables = [], this.vector_num_elems = 0, this.force_defaults = !1, this.string_maps = null, this.text_encoder = new TextEncoder();
    let e;
    t ? e = t : e = 1024, this.bb = ee.allocate(e), this.space = e;
  }
  clear() {
    this.bb.clear(), this.space = this.bb.capacity(), this.minalign = 1, this.vtable = null, this.vtable_in_use = 0, this.isNested = !1, this.object_start = 0, this.vtables = [], this.vector_num_elems = 0, this.force_defaults = !1, this.string_maps = null;
  }
  /**
   * In order to save space, fields that are set to their default value
   * don't get serialized into the buffer. Forcing defaults provides a
   * way to manually disable this optimization.
   *
   * @param forceDefaults true always serializes default values
   */
  forceDefaults(t) {
    this.force_defaults = t;
  }
  /**
   * Get the ByteBuffer representing the FlatBuffer. Only call this after you've
   * called finish(). The actual data starts at the ByteBuffer's current position,
   * not necessarily at 0.
   */
  dataBuffer() {
    return this.bb;
  }
  /**
   * Get the bytes representing the FlatBuffer. Only call this after you've
   * called finish().
   */
  asUint8Array() {
    return this.bb.bytes().subarray(this.bb.position(), this.bb.position() + this.offset());
  }
  /**
   * Prepare to write an element of `size` after `additional_bytes` have been
   * written, e.g. if you write a string, you need to align such the int length
   * field is aligned to 4 bytes, and the string data follows it directly. If all
   * you need to do is alignment, `additional_bytes` will be 0.
   *
   * @param size This is the of the new element to write
   * @param additional_bytes The padding size
   */
  prep(t, e) {
    t > this.minalign && (this.minalign = t);
    const i = ~(this.bb.capacity() - this.space + e) + 1 & t - 1;
    for (; this.space < i + t + e; ) {
      const s = this.bb.capacity();
      this.bb = Zs.growByteBuffer(this.bb), this.space += this.bb.capacity() - s;
    }
    this.pad(i);
  }
  pad(t) {
    for (let e = 0; e < t; e++)
      this.bb.writeInt8(--this.space, 0);
  }
  writeInt8(t) {
    this.bb.writeInt8(this.space -= 1, t);
  }
  writeInt16(t) {
    this.bb.writeInt16(this.space -= 2, t);
  }
  writeInt32(t) {
    this.bb.writeInt32(this.space -= 4, t);
  }
  writeInt64(t) {
    this.bb.writeInt64(this.space -= 8, t);
  }
  writeFloat32(t) {
    this.bb.writeFloat32(this.space -= 4, t);
  }
  writeFloat64(t) {
    this.bb.writeFloat64(this.space -= 8, t);
  }
  /**
   * Add an `int8` to the buffer, properly aligned, and grows the buffer (if necessary).
   * @param value The `int8` to add the buffer.
   */
  addInt8(t) {
    this.prep(1, 0), this.writeInt8(t);
  }
  /**
   * Add an `int16` to the buffer, properly aligned, and grows the buffer (if necessary).
   * @param value The `int16` to add the buffer.
   */
  addInt16(t) {
    this.prep(2, 0), this.writeInt16(t);
  }
  /**
   * Add an `int32` to the buffer, properly aligned, and grows the buffer (if necessary).
   * @param value The `int32` to add the buffer.
   */
  addInt32(t) {
    this.prep(4, 0), this.writeInt32(t);
  }
  /**
   * Add an `int64` to the buffer, properly aligned, and grows the buffer (if necessary).
   * @param value The `int64` to add the buffer.
   */
  addInt64(t) {
    this.prep(8, 0), this.writeInt64(t);
  }
  /**
   * Add a `float32` to the buffer, properly aligned, and grows the buffer (if necessary).
   * @param value The `float32` to add the buffer.
   */
  addFloat32(t) {
    this.prep(4, 0), this.writeFloat32(t);
  }
  /**
   * Add a `float64` to the buffer, properly aligned, and grows the buffer (if necessary).
   * @param value The `float64` to add the buffer.
   */
  addFloat64(t) {
    this.prep(8, 0), this.writeFloat64(t);
  }
  addFieldInt8(t, e, i) {
    (this.force_defaults || e != i) && (this.addInt8(e), this.slot(t));
  }
  addFieldInt16(t, e, i) {
    (this.force_defaults || e != i) && (this.addInt16(e), this.slot(t));
  }
  addFieldInt32(t, e, i) {
    (this.force_defaults || e != i) && (this.addInt32(e), this.slot(t));
  }
  addFieldInt64(t, e, i) {
    (this.force_defaults || e !== i) && (this.addInt64(e), this.slot(t));
  }
  addFieldFloat32(t, e, i) {
    (this.force_defaults || e != i) && (this.addFloat32(e), this.slot(t));
  }
  addFieldFloat64(t, e, i) {
    (this.force_defaults || e != i) && (this.addFloat64(e), this.slot(t));
  }
  addFieldOffset(t, e, i) {
    (this.force_defaults || e != i) && (this.addOffset(e), this.slot(t));
  }
  /**
   * Structs are stored inline, so nothing additional is being added. `d` is always 0.
   */
  addFieldStruct(t, e, i) {
    e != i && (this.nested(e), this.slot(t));
  }
  /**
   * Structures are always stored inline, they need to be created right
   * where they're used.  You'll get this assertion failure if you
   * created it elsewhere.
   */
  nested(t) {
    if (t != this.offset())
      throw new TypeError("FlatBuffers: struct must be serialized inline.");
  }
  /**
   * Should not be creating any other object, string or vector
   * while an object is being constructed
   */
  notNested() {
    if (this.isNested)
      throw new TypeError("FlatBuffers: object serialization must not be nested.");
  }
  /**
   * Set the current vtable at `voffset` to the current location in the buffer.
   */
  slot(t) {
    this.vtable !== null && (this.vtable[t] = this.offset());
  }
  /**
   * @returns Offset relative to the end of the buffer.
   */
  offset() {
    return this.bb.capacity() - this.space;
  }
  /**
   * Doubles the size of the backing ByteBuffer and copies the old data towards
   * the end of the new buffer (since we build the buffer backwards).
   *
   * @param bb The current buffer with the existing data
   * @returns A new byte buffer with the old data copied
   * to it. The data is located at the end of the buffer.
   *
   * uint8Array.set() formally takes {Array<number>|ArrayBufferView}, so to pass
   * it a uint8Array we need to suppress the type check:
   * @suppress {checkTypes}
   */
  static growByteBuffer(t) {
    const e = t.capacity();
    if (e & 3221225472)
      throw new Error("FlatBuffers: cannot grow buffer beyond 2 gigabytes.");
    const i = e << 1, s = ee.allocate(i);
    return s.setPosition(i - e), s.bytes().set(t.bytes(), i - e), s;
  }
  /**
   * Adds on offset, relative to where it will be written.
   *
   * @param offset The offset to add.
   */
  addOffset(t) {
    this.prep(Ot, 0), this.writeInt32(this.offset() - t + Ot);
  }
  /**
   * Start encoding a new object in the buffer.  Users will not usually need to
   * call this directly. The FlatBuffers compiler will generate helper methods
   * that call this method internally.
   */
  startObject(t) {
    this.notNested(), this.vtable == null && (this.vtable = []), this.vtable_in_use = t;
    for (let e = 0; e < t; e++)
      this.vtable[e] = 0;
    this.isNested = !0, this.object_start = this.offset();
  }
  /**
   * Finish off writing the object that is under construction.
   *
   * @returns The offset to the object inside `dataBuffer`
   */
  endObject() {
    if (this.vtable == null || !this.isNested)
      throw new Error("FlatBuffers: endObject called without startObject");
    this.addInt32(0);
    const t = this.offset();
    let e = this.vtable_in_use - 1;
    for (; e >= 0 && this.vtable[e] == 0; e--)
      ;
    const i = e + 1;
    for (; e >= 0; e--)
      this.addInt16(this.vtable[e] != 0 ? t - this.vtable[e] : 0);
    const s = 2;
    this.addInt16(t - this.object_start);
    const r = (i + s) * ii;
    this.addInt16(r);
    let o = 0;
    const a = this.space;
    t: for (e = 0; e < this.vtables.length; e++) {
      const c = this.bb.capacity() - this.vtables[e];
      if (r == this.bb.readInt16(c)) {
        for (let u = ii; u < r; u += ii)
          if (this.bb.readInt16(a + u) != this.bb.readInt16(c + u))
            continue t;
        o = this.vtables[e];
        break;
      }
    }
    return o ? (this.space = this.bb.capacity() - t, this.bb.writeInt32(this.space, o - t)) : (this.vtables.push(this.offset()), this.bb.writeInt32(this.bb.capacity() - t, this.offset() - t)), this.isNested = !1, t;
  }
  /**
   * Finalize a buffer, poiting to the given `root_table`.
   */
  finish(t, e, i) {
    const s = i ? E : 0;
    if (e) {
      const r = e;
      if (this.prep(this.minalign, Ot + Ct + s), r.length != Ct)
        throw new TypeError("FlatBuffers: file identifier must be length " + Ct);
      for (let o = Ct - 1; o >= 0; o--)
        this.writeInt8(r.charCodeAt(o));
    }
    this.prep(this.minalign, Ot + s), this.addOffset(t), s && this.addInt32(this.bb.capacity() - this.space), this.bb.setPosition(this.space);
  }
  /**
   * Finalize a size prefixed buffer, pointing to the given `root_table`.
   */
  finishSizePrefixed(t, e) {
    this.finish(t, e, !0);
  }
  /**
   * This checks a required field has been set in a given table that has
   * just been constructed.
   */
  requiredField(t, e) {
    const i = this.bb.capacity() - t, s = i - this.bb.readInt32(i);
    if (!(e < this.bb.readInt16(s) && this.bb.readInt16(s + e) != 0))
      throw new TypeError("FlatBuffers: field " + e + " must be set");
  }
  /**
   * Start a new array/vector of objects.  Users usually will not call
   * this directly. The FlatBuffers compiler will create a start/end
   * method for vector types in generated code.
   *
   * @param elem_size The size of each element in the array
   * @param num_elems The number of elements in the array
   * @param alignment The alignment of the array
   */
  startVector(t, e, i) {
    this.notNested(), this.vector_num_elems = e, this.prep(Ot, t * e), this.prep(i, t * e);
  }
  /**
   * Finish off the creation of an array and all its elements. The array must be
   * created with `startVector`.
   *
   * @returns The offset at which the newly created array
   * starts.
   */
  endVector() {
    return this.writeInt32(this.vector_num_elems), this.offset();
  }
  /**
   * Encode the string `s` in the buffer using UTF-8. If the string passed has
   * already been seen, we return the offset of the already written string
   *
   * @param s The string to encode
   * @return The offset in the buffer where the encoded string starts
   */
  createSharedString(t) {
    if (!t)
      return 0;
    if (this.string_maps || (this.string_maps = /* @__PURE__ */ new Map()), this.string_maps.has(t))
      return this.string_maps.get(t);
    const e = this.createString(t);
    return this.string_maps.set(t, e), e;
  }
  /**
   * Encode the string `s` in the buffer using UTF-8. If a Uint8Array is passed
   * instead of a string, it is assumed to contain valid UTF-8 encoded data.
   *
   * @param s The string to encode
   * @return The offset in the buffer where the encoded string starts
   */
  createString(t) {
    if (t == null)
      return 0;
    let e;
    return t instanceof Uint8Array ? e = t : e = this.text_encoder.encode(t), this.addInt8(0), this.startVector(1, e.length, 1), this.bb.setPosition(this.space -= e.length), this.bb.bytes().set(e, this.space), this.endVector();
  }
  /**
   * Create a byte vector.
   *
   * @param v The bytes to add
   * @returns The offset in the buffer where the byte vector starts
   */
  createByteVector(t) {
    return t == null ? 0 : (this.startVector(1, t.length, 1), this.bb.setPosition(this.space -= t.length), this.bb.bytes().set(t, this.space), this.endVector());
  }
  /**
   * A helper function to pack an object
   *
   * @returns offset of obj
   */
  createObjectOffset(t) {
    return t === null ? 0 : typeof t == "string" ? this.createString(t) : t.pack(this);
  }
  /**
   * A helper function to pack a list of object
   *
   * @returns list of offsets of each non null object
   */
  createObjectOffsetList(t) {
    const e = [];
    for (let i = 0; i < t.length; ++i) {
      const s = t[i];
      if (s !== null)
        e.push(this.createObjectOffset(s));
      else
        throw new TypeError("FlatBuffers: Argument for createObjectOffsetList cannot contain null.");
    }
    return e;
  }
  createStructOffsetList(t, e) {
    return e(this, t.length), this.createObjectOffsetList(t.slice().reverse()), this.endVector();
  }
};
var Je;
(function(n) {
  n[n.BUFFER = 0] = "BUFFER";
})(Je || (Je = {}));
var ne;
(function(n) {
  n[n.LZ4_FRAME = 0] = "LZ4_FRAME", n[n.ZSTD = 1] = "ZSTD";
})(ne || (ne = {}));
let Re = class Qt {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsBodyCompression(t, e) {
    return (e || new Qt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsBodyCompression(t, e) {
    return t.setPosition(t.position() + E), (e || new Qt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  /**
   * Compressor library.
   * For LZ4_FRAME, each compressed buffer must consist of a single frame.
   */
  codec() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt8(this.bb_pos + t) : ne.LZ4_FRAME;
  }
  /**
   * Indicates the way the record batch body was compressed
   */
  method() {
    const t = this.bb.__offset(this.bb_pos, 6);
    return t ? this.bb.readInt8(this.bb_pos + t) : Je.BUFFER;
  }
  static startBodyCompression(t) {
    t.startObject(2);
  }
  static addCodec(t, e) {
    t.addFieldInt8(0, e, ne.LZ4_FRAME);
  }
  static addMethod(t, e) {
    t.addFieldInt8(1, e, Je.BUFFER);
  }
  static endBodyCompression(t) {
    return t.endObject();
  }
  static createBodyCompression(t, e, i) {
    return Qt.startBodyCompression(t), Qt.addCodec(t, e), Qt.addMethod(t, i), Qt.endBodyCompression(t);
  }
};
class Qs {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  /**
   * The relative offset into the shared memory page where the bytes for this
   * buffer starts
   */
  offset() {
    return this.bb.readInt64(this.bb_pos);
  }
  /**
   * The absolute length (in bytes) of the memory buffer. The memory is found
   * from offset (inclusive) to offset + length (non-inclusive). When building
   * messages using the encapsulated IPC message, padding bytes may be written
   * after a buffer, but such padding bytes do not need to be accounted for in
   * the size here.
   */
  length() {
    return this.bb.readInt64(this.bb_pos + 8);
  }
  static sizeOf() {
    return 16;
  }
  static createBuffer(t, e, i) {
    return t.prep(8, 16), t.writeInt64(BigInt(i ?? 0)), t.writeInt64(BigInt(e ?? 0)), t.offset();
  }
}
let Xs = class {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  /**
   * The number of value slots in the Arrow array at this level of a nested
   * tree
   */
  length() {
    return this.bb.readInt64(this.bb_pos);
  }
  /**
   * The number of observed nulls. Fields with null_count == 0 may choose not
   * to write their physical validity bitmap out as a materialized buffer,
   * instead setting the length of the bitmap buffer to 0.
   */
  nullCount() {
    return this.bb.readInt64(this.bb_pos + 8);
  }
  static sizeOf() {
    return 16;
  }
  static createFieldNode(t, e, i) {
    return t.prep(8, 16), t.writeInt64(BigInt(i ?? 0)), t.writeInt64(BigInt(e ?? 0)), t.offset();
  }
}, St = class yi {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsRecordBatch(t, e) {
    return (e || new yi()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsRecordBatch(t, e) {
    return t.setPosition(t.position() + E), (e || new yi()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  /**
   * number of records / rows. The arrays in the batch should all have this
   * length
   */
  length() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt64(this.bb_pos + t) : BigInt("0");
  }
  /**
   * Nodes correspond to the pre-ordered flattened logical schema
   */
  nodes(t, e) {
    const i = this.bb.__offset(this.bb_pos, 6);
    return i ? (e || new Xs()).__init(this.bb.__vector(this.bb_pos + i) + t * 16, this.bb) : null;
  }
  nodesLength() {
    const t = this.bb.__offset(this.bb_pos, 6);
    return t ? this.bb.__vector_len(this.bb_pos + t) : 0;
  }
  /**
   * Buffers correspond to the pre-ordered flattened buffer tree
   *
   * The number of buffers appended to this list depends on the schema. For
   * example, most primitive arrays will have 2 buffers, 1 for the validity
   * bitmap and 1 for the values. For struct arrays, there will only be a
   * single buffer for the validity (nulls) bitmap
   */
  buffers(t, e) {
    const i = this.bb.__offset(this.bb_pos, 8);
    return i ? (e || new Qs()).__init(this.bb.__vector(this.bb_pos + i) + t * 16, this.bb) : null;
  }
  buffersLength() {
    const t = this.bb.__offset(this.bb_pos, 8);
    return t ? this.bb.__vector_len(this.bb_pos + t) : 0;
  }
  /**
   * Optional compression of the message body
   */
  compression(t) {
    const e = this.bb.__offset(this.bb_pos, 10);
    return e ? (t || new Re()).__init(this.bb.__indirect(this.bb_pos + e), this.bb) : null;
  }
  static startRecordBatch(t) {
    t.startObject(4);
  }
  static addLength(t, e) {
    t.addFieldInt64(0, e, BigInt("0"));
  }
  static addNodes(t, e) {
    t.addFieldOffset(1, e, 0);
  }
  static startNodesVector(t, e) {
    t.startVector(16, e, 8);
  }
  static addBuffers(t, e) {
    t.addFieldOffset(2, e, 0);
  }
  static startBuffersVector(t, e) {
    t.startVector(16, e, 8);
  }
  static addCompression(t, e) {
    t.addFieldOffset(3, e, 0);
  }
  static endRecordBatch(t) {
    return t.endObject();
  }
}, ae = class gi {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsDictionaryBatch(t, e) {
    return (e || new gi()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsDictionaryBatch(t, e) {
    return t.setPosition(t.position() + E), (e || new gi()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  id() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt64(this.bb_pos + t) : BigInt("0");
  }
  data(t) {
    const e = this.bb.__offset(this.bb_pos, 6);
    return e ? (t || new St()).__init(this.bb.__indirect(this.bb_pos + e), this.bb) : null;
  }
  /**
   * If isDelta is true the values in the dictionary are to be appended to a
   * dictionary with the indicated id. If isDelta is false this dictionary
   * should replace the existing dictionary.
   */
  isDelta() {
    const t = this.bb.__offset(this.bb_pos, 8);
    return t ? !!this.bb.readInt8(this.bb_pos + t) : !1;
  }
  static startDictionaryBatch(t) {
    t.startObject(3);
  }
  static addId(t, e) {
    t.addFieldInt64(0, e, BigInt("0"));
  }
  static addData(t, e) {
    t.addFieldOffset(1, e, 0);
  }
  static addIsDelta(t, e) {
    t.addFieldInt8(2, +e, 0);
  }
  static endDictionaryBatch(t) {
    return t.endObject();
  }
};
var Be;
(function(n) {
  n[n.Little = 0] = "Little", n[n.Big = 1] = "Big";
})(Be || (Be = {}));
var Dn;
(function(n) {
  n[n.DenseArray = 0] = "DenseArray";
})(Dn || (Dn = {}));
class rt {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsInt(t, e) {
    return (e || new rt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsInt(t, e) {
    return t.setPosition(t.position() + E), (e || new rt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  bitWidth() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt32(this.bb_pos + t) : 0;
  }
  isSigned() {
    const t = this.bb.__offset(this.bb_pos, 6);
    return t ? !!this.bb.readInt8(this.bb_pos + t) : !1;
  }
  static startInt(t) {
    t.startObject(2);
  }
  static addBitWidth(t, e) {
    t.addFieldInt32(0, e, 0);
  }
  static addIsSigned(t, e) {
    t.addFieldInt8(1, +e, 0);
  }
  static endInt(t) {
    return t.endObject();
  }
  static createInt(t, e, i) {
    return rt.startInt(t), rt.addBitWidth(t, e), rt.addIsSigned(t, i), rt.endInt(t);
  }
}
class Et {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsDictionaryEncoding(t, e) {
    return (e || new Et()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsDictionaryEncoding(t, e) {
    return t.setPosition(t.position() + E), (e || new Et()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  /**
   * The known dictionary id in the application where this data is used. In
   * the file or streaming formats, the dictionary ids are found in the
   * DictionaryBatch messages
   */
  id() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt64(this.bb_pos + t) : BigInt("0");
  }
  /**
   * The dictionary indices are constrained to be non-negative integers. If
   * this field is null, the indices must be signed int32. To maximize
   * cross-language compatibility and performance, implementations are
   * recommended to prefer signed integer types over unsigned integer types
   * and to avoid uint64 indices unless they are required by an application.
   */
  indexType(t) {
    const e = this.bb.__offset(this.bb_pos, 6);
    return e ? (t || new rt()).__init(this.bb.__indirect(this.bb_pos + e), this.bb) : null;
  }
  /**
   * By default, dictionaries are not ordered, or the order does not have
   * semantic meaning. In some statistical, applications, dictionary-encoding
   * is used to represent ordered categorical data, and we provide a way to
   * preserve that metadata here
   */
  isOrdered() {
    const t = this.bb.__offset(this.bb_pos, 8);
    return t ? !!this.bb.readInt8(this.bb_pos + t) : !1;
  }
  dictionaryKind() {
    const t = this.bb.__offset(this.bb_pos, 10);
    return t ? this.bb.readInt16(this.bb_pos + t) : Dn.DenseArray;
  }
  static startDictionaryEncoding(t) {
    t.startObject(4);
  }
  static addId(t, e) {
    t.addFieldInt64(0, e, BigInt("0"));
  }
  static addIndexType(t, e) {
    t.addFieldOffset(1, e, 0);
  }
  static addIsOrdered(t, e) {
    t.addFieldInt8(2, +e, 0);
  }
  static addDictionaryKind(t, e) {
    t.addFieldInt16(3, e, Dn.DenseArray);
  }
  static endDictionaryEncoding(t) {
    return t.endObject();
  }
}
class H {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsKeyValue(t, e) {
    return (e || new H()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsKeyValue(t, e) {
    return t.setPosition(t.position() + E), (e || new H()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  key(t) {
    const e = this.bb.__offset(this.bb_pos, 4);
    return e ? this.bb.__string(this.bb_pos + e, t) : null;
  }
  value(t) {
    const e = this.bb.__offset(this.bb_pos, 6);
    return e ? this.bb.__string(this.bb_pos + e, t) : null;
  }
  static startKeyValue(t) {
    t.startObject(2);
  }
  static addKey(t, e) {
    t.addFieldOffset(0, e, 0);
  }
  static addValue(t, e) {
    t.addFieldOffset(1, e, 0);
  }
  static endKeyValue(t) {
    return t.endObject();
  }
  static createKeyValue(t, e, i) {
    return H.startKeyValue(t), H.addKey(t, e), H.addValue(t, i), H.endKeyValue(t);
  }
}
let gs = class ze {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsBinary(t, e) {
    return (e || new ze()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsBinary(t, e) {
    return t.setPosition(t.position() + E), (e || new ze()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static startBinary(t) {
    t.startObject(0);
  }
  static endBinary(t) {
    return t.endObject();
  }
  static createBinary(t) {
    return ze.startBinary(t), ze.endBinary(t);
  }
}, ms = class ke {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsBool(t, e) {
    return (e || new ke()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsBool(t, e) {
    return t.setPosition(t.position() + E), (e || new ke()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static startBool(t) {
    t.startObject(0);
  }
  static endBool(t) {
    return t.endObject();
  }
  static createBool(t) {
    return ke.startBool(t), ke.endBool(t);
  }
}, mn = class ce {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsDate(t, e) {
    return (e || new ce()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsDate(t, e) {
    return t.setPosition(t.position() + E), (e || new ce()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  unit() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt16(this.bb_pos + t) : ft.MILLISECOND;
  }
  static startDate(t) {
    t.startObject(1);
  }
  static addUnit(t, e) {
    t.addFieldInt16(0, e, ft.MILLISECOND);
  }
  static endDate(t) {
    return t.endObject();
  }
  static createDate(t, e) {
    return ce.startDate(t), ce.addUnit(t, e), ce.endDate(t);
  }
}, le = class Wt {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsDecimal(t, e) {
    return (e || new Wt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsDecimal(t, e) {
    return t.setPosition(t.position() + E), (e || new Wt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  /**
   * Total number of decimal digits
   */
  precision() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt32(this.bb_pos + t) : 0;
  }
  /**
   * Number of digits after the decimal point "."
   */
  scale() {
    const t = this.bb.__offset(this.bb_pos, 6);
    return t ? this.bb.readInt32(this.bb_pos + t) : 0;
  }
  /**
   * Number of bits per value. The only accepted widths are 128 and 256.
   * We use bitWidth for consistency with Int::bitWidth.
   */
  bitWidth() {
    const t = this.bb.__offset(this.bb_pos, 8);
    return t ? this.bb.readInt32(this.bb_pos + t) : 128;
  }
  static startDecimal(t) {
    t.startObject(3);
  }
  static addPrecision(t, e) {
    t.addFieldInt32(0, e, 0);
  }
  static addScale(t, e) {
    t.addFieldInt32(1, e, 0);
  }
  static addBitWidth(t, e) {
    t.addFieldInt32(2, e, 128);
  }
  static endDecimal(t) {
    return t.endObject();
  }
  static createDecimal(t, e, i, s) {
    return Wt.startDecimal(t), Wt.addPrecision(t, e), Wt.addScale(t, i), Wt.addBitWidth(t, s), Wt.endDecimal(t);
  }
}, bn = class ue {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsDuration(t, e) {
    return (e || new ue()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsDuration(t, e) {
    return t.setPosition(t.position() + E), (e || new ue()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  unit() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt16(this.bb_pos + t) : b.MILLISECOND;
  }
  static startDuration(t) {
    t.startObject(1);
  }
  static addUnit(t, e) {
    t.addFieldInt16(0, e, b.MILLISECOND);
  }
  static endDuration(t) {
    return t.endObject();
  }
  static createDuration(t, e) {
    return ue.startDuration(t), ue.addUnit(t, e), ue.endDuration(t);
  }
}, _n = class de {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsFixedSizeBinary(t, e) {
    return (e || new de()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsFixedSizeBinary(t, e) {
    return t.setPosition(t.position() + E), (e || new de()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  /**
   * Number of bytes per value
   */
  byteWidth() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt32(this.bb_pos + t) : 0;
  }
  static startFixedSizeBinary(t) {
    t.startObject(1);
  }
  static addByteWidth(t, e) {
    t.addFieldInt32(0, e, 0);
  }
  static endFixedSizeBinary(t) {
    return t.endObject();
  }
  static createFixedSizeBinary(t, e) {
    return de.startFixedSizeBinary(t), de.addByteWidth(t, e), de.endFixedSizeBinary(t);
  }
}, vn = class he {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsFixedSizeList(t, e) {
    return (e || new he()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsFixedSizeList(t, e) {
    return t.setPosition(t.position() + E), (e || new he()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  /**
   * Number of list items per value
   */
  listSize() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt32(this.bb_pos + t) : 0;
  }
  static startFixedSizeList(t) {
    t.startObject(1);
  }
  static addListSize(t, e) {
    t.addFieldInt32(0, e, 0);
  }
  static endFixedSizeList(t) {
    return t.endObject();
  }
  static createFixedSizeList(t, e) {
    return he.startFixedSizeList(t), he.addListSize(t, e), he.endFixedSizeList(t);
  }
};
class Ft {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsFloatingPoint(t, e) {
    return (e || new Ft()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsFloatingPoint(t, e) {
    return t.setPosition(t.position() + E), (e || new Ft()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  precision() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt16(this.bb_pos + t) : W.HALF;
  }
  static startFloatingPoint(t) {
    t.startObject(1);
  }
  static addPrecision(t, e) {
    t.addFieldInt16(0, e, W.HALF);
  }
  static endFloatingPoint(t) {
    return t.endObject();
  }
  static createFloatingPoint(t, e) {
    return Ft.startFloatingPoint(t), Ft.addPrecision(t, e), Ft.endFloatingPoint(t);
  }
}
class Mt {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsInterval(t, e) {
    return (e || new Mt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsInterval(t, e) {
    return t.setPosition(t.position() + E), (e || new Mt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  unit() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt16(this.bb_pos + t) : J.YEAR_MONTH;
  }
  static startInterval(t) {
    t.startObject(1);
  }
  static addUnit(t, e) {
    t.addFieldInt16(0, e, J.YEAR_MONTH);
  }
  static endInterval(t) {
    return t.endObject();
  }
  static createInterval(t, e) {
    return Mt.startInterval(t), Mt.addUnit(t, e), Mt.endInterval(t);
  }
}
let bs = class Pe {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsLargeBinary(t, e) {
    return (e || new Pe()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsLargeBinary(t, e) {
    return t.setPosition(t.position() + E), (e || new Pe()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static startLargeBinary(t) {
    t.startObject(0);
  }
  static endLargeBinary(t) {
    return t.endObject();
  }
  static createLargeBinary(t) {
    return Pe.startLargeBinary(t), Pe.endLargeBinary(t);
  }
}, _s = class je {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsLargeUtf8(t, e) {
    return (e || new je()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsLargeUtf8(t, e) {
    return t.setPosition(t.position() + E), (e || new je()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static startLargeUtf8(t) {
    t.startObject(0);
  }
  static endLargeUtf8(t) {
    return t.endObject();
  }
  static createLargeUtf8(t) {
    return je.startLargeUtf8(t), je.endLargeUtf8(t);
  }
}, vs = class $e {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsList(t, e) {
    return (e || new $e()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsList(t, e) {
    return t.setPosition(t.position() + E), (e || new $e()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static startList(t) {
    t.startObject(0);
  }
  static endList(t) {
    return t.endObject();
  }
  static createList(t) {
    return $e.startList(t), $e.endList(t);
  }
}, wn = class fe {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsMap(t, e) {
    return (e || new fe()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsMap(t, e) {
    return t.setPosition(t.position() + E), (e || new fe()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  /**
   * Set to true if the keys within each value are sorted
   */
  keysSorted() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? !!this.bb.readInt8(this.bb_pos + t) : !1;
  }
  static startMap(t) {
    t.startObject(1);
  }
  static addKeysSorted(t, e) {
    t.addFieldInt8(0, +e, 0);
  }
  static endMap(t) {
    return t.endObject();
  }
  static createMap(t, e) {
    return fe.startMap(t), fe.addKeysSorted(t, e), fe.endMap(t);
  }
}, ws = class Ye {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsNull(t, e) {
    return (e || new Ye()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsNull(t, e) {
    return t.setPosition(t.position() + E), (e || new Ye()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static startNull(t) {
    t.startObject(0);
  }
  static endNull(t) {
    return t.endObject();
  }
  static createNull(t) {
    return Ye.startNull(t), Ye.endNull(t);
  }
};
class te {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsStruct_(t, e) {
    return (e || new te()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsStruct_(t, e) {
    return t.setPosition(t.position() + E), (e || new te()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static startStruct_(t) {
    t.startObject(0);
  }
  static endStruct_(t) {
    return t.endObject();
  }
  static createStruct_(t) {
    return te.startStruct_(t), te.endStruct_(t);
  }
}
class dt {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsTime(t, e) {
    return (e || new dt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsTime(t, e) {
    return t.setPosition(t.position() + E), (e || new dt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  unit() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt16(this.bb_pos + t) : b.MILLISECOND;
  }
  bitWidth() {
    const t = this.bb.__offset(this.bb_pos, 6);
    return t ? this.bb.readInt32(this.bb_pos + t) : 32;
  }
  static startTime(t) {
    t.startObject(2);
  }
  static addUnit(t, e) {
    t.addFieldInt16(0, e, b.MILLISECOND);
  }
  static addBitWidth(t, e) {
    t.addFieldInt32(1, e, 32);
  }
  static endTime(t) {
    return t.endObject();
  }
  static createTime(t, e, i) {
    return dt.startTime(t), dt.addUnit(t, e), dt.addBitWidth(t, i), dt.endTime(t);
  }
}
class ht {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsTimestamp(t, e) {
    return (e || new ht()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsTimestamp(t, e) {
    return t.setPosition(t.position() + E), (e || new ht()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  unit() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt16(this.bb_pos + t) : b.SECOND;
  }
  timezone(t) {
    const e = this.bb.__offset(this.bb_pos, 6);
    return e ? this.bb.__string(this.bb_pos + e, t) : null;
  }
  static startTimestamp(t) {
    t.startObject(2);
  }
  static addUnit(t, e) {
    t.addFieldInt16(0, e, b.SECOND);
  }
  static addTimezone(t, e) {
    t.addFieldOffset(1, e, 0);
  }
  static endTimestamp(t) {
    return t.endObject();
  }
  static createTimestamp(t, e, i) {
    return ht.startTimestamp(t), ht.addUnit(t, e), ht.addTimezone(t, i), ht.endTimestamp(t);
  }
}
class X {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsUnion(t, e) {
    return (e || new X()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsUnion(t, e) {
    return t.setPosition(t.position() + E), (e || new X()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  mode() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt16(this.bb_pos + t) : tt.Sparse;
  }
  typeIds(t) {
    const e = this.bb.__offset(this.bb_pos, 6);
    return e ? this.bb.readInt32(this.bb.__vector(this.bb_pos + e) + t * 4) : 0;
  }
  typeIdsLength() {
    const t = this.bb.__offset(this.bb_pos, 6);
    return t ? this.bb.__vector_len(this.bb_pos + t) : 0;
  }
  typeIdsArray() {
    const t = this.bb.__offset(this.bb_pos, 6);
    return t ? new Int32Array(this.bb.bytes().buffer, this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + t), this.bb.__vector_len(this.bb_pos + t)) : null;
  }
  static startUnion(t) {
    t.startObject(2);
  }
  static addMode(t, e) {
    t.addFieldInt16(0, e, tt.Sparse);
  }
  static addTypeIds(t, e) {
    t.addFieldOffset(1, e, 0);
  }
  static createTypeIdsVector(t, e) {
    t.startVector(4, e.length, 4);
    for (let i = e.length - 1; i >= 0; i--)
      t.addInt32(e[i]);
    return t.endVector();
  }
  static startTypeIdsVector(t, e) {
    t.startVector(4, e, 4);
  }
  static endUnion(t) {
    return t.endObject();
  }
  static createUnion(t, e, i) {
    return X.startUnion(t), X.addMode(t, e), X.addTypeIds(t, i), X.endUnion(t);
  }
}
let Is = class He {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsUtf8(t, e) {
    return (e || new He()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsUtf8(t, e) {
    return t.setPosition(t.position() + E), (e || new He()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static startUtf8(t) {
    t.startObject(0);
  }
  static endUtf8(t) {
    return t.endObject();
  }
  static createUtf8(t) {
    return He.startUtf8(t), He.endUtf8(t);
  }
};
var k;
(function(n) {
  n[n.NONE = 0] = "NONE", n[n.Null = 1] = "Null", n[n.Int = 2] = "Int", n[n.FloatingPoint = 3] = "FloatingPoint", n[n.Binary = 4] = "Binary", n[n.Utf8 = 5] = "Utf8", n[n.Bool = 6] = "Bool", n[n.Decimal = 7] = "Decimal", n[n.Date = 8] = "Date", n[n.Time = 9] = "Time", n[n.Timestamp = 10] = "Timestamp", n[n.Interval = 11] = "Interval", n[n.List = 12] = "List", n[n.Struct_ = 13] = "Struct_", n[n.Union = 14] = "Union", n[n.FixedSizeBinary = 15] = "FixedSizeBinary", n[n.FixedSizeList = 16] = "FixedSizeList", n[n.Map = 17] = "Map", n[n.Duration = 18] = "Duration", n[n.LargeBinary = 19] = "LargeBinary", n[n.LargeUtf8 = 20] = "LargeUtf8", n[n.LargeList = 21] = "LargeList", n[n.RunEndEncoded = 22] = "RunEndEncoded";
})(k || (k = {}));
let lt = class In {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsField(t, e) {
    return (e || new In()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsField(t, e) {
    return t.setPosition(t.position() + E), (e || new In()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  name(t) {
    const e = this.bb.__offset(this.bb_pos, 4);
    return e ? this.bb.__string(this.bb_pos + e, t) : null;
  }
  /**
   * Whether or not this field can contain nulls. Should be true in general.
   */
  nullable() {
    const t = this.bb.__offset(this.bb_pos, 6);
    return t ? !!this.bb.readInt8(this.bb_pos + t) : !1;
  }
  typeType() {
    const t = this.bb.__offset(this.bb_pos, 8);
    return t ? this.bb.readUint8(this.bb_pos + t) : k.NONE;
  }
  /**
   * This is the type of the decoded value if the field is dictionary encoded.
   */
  type(t) {
    const e = this.bb.__offset(this.bb_pos, 10);
    return e ? this.bb.__union(t, this.bb_pos + e) : null;
  }
  /**
   * Present only if the field is dictionary encoded.
   */
  dictionary(t) {
    const e = this.bb.__offset(this.bb_pos, 12);
    return e ? (t || new Et()).__init(this.bb.__indirect(this.bb_pos + e), this.bb) : null;
  }
  /**
   * children apply only to nested data types like Struct, List and Union. For
   * primitive types children will have length 0.
   */
  children(t, e) {
    const i = this.bb.__offset(this.bb_pos, 14);
    return i ? (e || new In()).__init(this.bb.__indirect(this.bb.__vector(this.bb_pos + i) + t * 4), this.bb) : null;
  }
  childrenLength() {
    const t = this.bb.__offset(this.bb_pos, 14);
    return t ? this.bb.__vector_len(this.bb_pos + t) : 0;
  }
  /**
   * User-defined metadata
   */
  customMetadata(t, e) {
    const i = this.bb.__offset(this.bb_pos, 16);
    return i ? (e || new H()).__init(this.bb.__indirect(this.bb.__vector(this.bb_pos + i) + t * 4), this.bb) : null;
  }
  customMetadataLength() {
    const t = this.bb.__offset(this.bb_pos, 16);
    return t ? this.bb.__vector_len(this.bb_pos + t) : 0;
  }
  static startField(t) {
    t.startObject(7);
  }
  static addName(t, e) {
    t.addFieldOffset(0, e, 0);
  }
  static addNullable(t, e) {
    t.addFieldInt8(1, +e, 0);
  }
  static addTypeType(t, e) {
    t.addFieldInt8(2, e, k.NONE);
  }
  static addType(t, e) {
    t.addFieldOffset(3, e, 0);
  }
  static addDictionary(t, e) {
    t.addFieldOffset(4, e, 0);
  }
  static addChildren(t, e) {
    t.addFieldOffset(5, e, 0);
  }
  static createChildrenVector(t, e) {
    t.startVector(4, e.length, 4);
    for (let i = e.length - 1; i >= 0; i--)
      t.addOffset(e[i]);
    return t.endVector();
  }
  static startChildrenVector(t, e) {
    t.startVector(4, e, 4);
  }
  static addCustomMetadata(t, e) {
    t.addFieldOffset(6, e, 0);
  }
  static createCustomMetadataVector(t, e) {
    t.startVector(4, e.length, 4);
    for (let i = e.length - 1; i >= 0; i--)
      t.addOffset(e[i]);
    return t.endVector();
  }
  static startCustomMetadataVector(t, e) {
    t.startVector(4, e, 4);
  }
  static endField(t) {
    return t.endObject();
  }
}, Bt = class xt {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsSchema(t, e) {
    return (e || new xt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsSchema(t, e) {
    return t.setPosition(t.position() + E), (e || new xt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  /**
   * endianness of the buffer
   * it is Little Endian by default
   * if endianness doesn't match the underlying system then the vectors need to be converted
   */
  endianness() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt16(this.bb_pos + t) : Be.Little;
  }
  fields(t, e) {
    const i = this.bb.__offset(this.bb_pos, 6);
    return i ? (e || new lt()).__init(this.bb.__indirect(this.bb.__vector(this.bb_pos + i) + t * 4), this.bb) : null;
  }
  fieldsLength() {
    const t = this.bb.__offset(this.bb_pos, 6);
    return t ? this.bb.__vector_len(this.bb_pos + t) : 0;
  }
  customMetadata(t, e) {
    const i = this.bb.__offset(this.bb_pos, 8);
    return i ? (e || new H()).__init(this.bb.__indirect(this.bb.__vector(this.bb_pos + i) + t * 4), this.bb) : null;
  }
  customMetadataLength() {
    const t = this.bb.__offset(this.bb_pos, 8);
    return t ? this.bb.__vector_len(this.bb_pos + t) : 0;
  }
  /**
   * Features used in the stream/file.
   */
  features(t) {
    const e = this.bb.__offset(this.bb_pos, 10);
    return e ? this.bb.readInt64(this.bb.__vector(this.bb_pos + e) + t * 8) : BigInt(0);
  }
  featuresLength() {
    const t = this.bb.__offset(this.bb_pos, 10);
    return t ? this.bb.__vector_len(this.bb_pos + t) : 0;
  }
  static startSchema(t) {
    t.startObject(4);
  }
  static addEndianness(t, e) {
    t.addFieldInt16(0, e, Be.Little);
  }
  static addFields(t, e) {
    t.addFieldOffset(1, e, 0);
  }
  static createFieldsVector(t, e) {
    t.startVector(4, e.length, 4);
    for (let i = e.length - 1; i >= 0; i--)
      t.addOffset(e[i]);
    return t.endVector();
  }
  static startFieldsVector(t, e) {
    t.startVector(4, e, 4);
  }
  static addCustomMetadata(t, e) {
    t.addFieldOffset(2, e, 0);
  }
  static createCustomMetadataVector(t, e) {
    t.startVector(4, e.length, 4);
    for (let i = e.length - 1; i >= 0; i--)
      t.addOffset(e[i]);
    return t.endVector();
  }
  static startCustomMetadataVector(t, e) {
    t.startVector(4, e, 4);
  }
  static addFeatures(t, e) {
    t.addFieldOffset(3, e, 0);
  }
  static createFeaturesVector(t, e) {
    t.startVector(8, e.length, 8);
    for (let i = e.length - 1; i >= 0; i--)
      t.addInt64(e[i]);
    return t.endVector();
  }
  static startFeaturesVector(t, e) {
    t.startVector(8, e, 8);
  }
  static endSchema(t) {
    return t.endObject();
  }
  static finishSchemaBuffer(t, e) {
    t.finish(e);
  }
  static finishSizePrefixedSchemaBuffer(t, e) {
    t.finish(e, void 0, !0);
  }
  static createSchema(t, e, i, s, r) {
    return xt.startSchema(t), xt.addEndianness(t, e), xt.addFields(t, i), xt.addCustomMetadata(t, s), xt.addFeatures(t, r), xt.endSchema(t);
  }
};
var U;
(function(n) {
  n[n.NONE = 0] = "NONE", n[n.Schema = 1] = "Schema", n[n.DictionaryBatch = 2] = "DictionaryBatch", n[n.RecordBatch = 3] = "RecordBatch", n[n.Tensor = 4] = "Tensor", n[n.SparseTensor = 5] = "SparseTensor";
})(U || (U = {}));
var l;
(function(n) {
  n[n.NONE = 0] = "NONE", n[n.Null = 1] = "Null", n[n.Int = 2] = "Int", n[n.Float = 3] = "Float", n[n.Binary = 4] = "Binary", n[n.Utf8 = 5] = "Utf8", n[n.Bool = 6] = "Bool", n[n.Decimal = 7] = "Decimal", n[n.Date = 8] = "Date", n[n.Time = 9] = "Time", n[n.Timestamp = 10] = "Timestamp", n[n.Interval = 11] = "Interval", n[n.List = 12] = "List", n[n.Struct = 13] = "Struct", n[n.Union = 14] = "Union", n[n.FixedSizeBinary = 15] = "FixedSizeBinary", n[n.FixedSizeList = 16] = "FixedSizeList", n[n.Map = 17] = "Map", n[n.Duration = 18] = "Duration", n[n.LargeBinary = 19] = "LargeBinary", n[n.LargeUtf8 = 20] = "LargeUtf8", n[n.Dictionary = -1] = "Dictionary", n[n.Int8 = -2] = "Int8", n[n.Int16 = -3] = "Int16", n[n.Int32 = -4] = "Int32", n[n.Int64 = -5] = "Int64", n[n.Uint8 = -6] = "Uint8", n[n.Uint16 = -7] = "Uint16", n[n.Uint32 = -8] = "Uint32", n[n.Uint64 = -9] = "Uint64", n[n.Float16 = -10] = "Float16", n[n.Float32 = -11] = "Float32", n[n.Float64 = -12] = "Float64", n[n.DateDay = -13] = "DateDay", n[n.DateMillisecond = -14] = "DateMillisecond", n[n.TimestampSecond = -15] = "TimestampSecond", n[n.TimestampMillisecond = -16] = "TimestampMillisecond", n[n.TimestampMicrosecond = -17] = "TimestampMicrosecond", n[n.TimestampNanosecond = -18] = "TimestampNanosecond", n[n.TimeSecond = -19] = "TimeSecond", n[n.TimeMillisecond = -20] = "TimeMillisecond", n[n.TimeMicrosecond = -21] = "TimeMicrosecond", n[n.TimeNanosecond = -22] = "TimeNanosecond", n[n.DenseUnion = -23] = "DenseUnion", n[n.SparseUnion = -24] = "SparseUnion", n[n.IntervalDayTime = -25] = "IntervalDayTime", n[n.IntervalYearMonth = -26] = "IntervalYearMonth", n[n.DurationSecond = -27] = "DurationSecond", n[n.DurationMillisecond = -28] = "DurationMillisecond", n[n.DurationMicrosecond = -29] = "DurationMicrosecond", n[n.DurationNanosecond = -30] = "DurationNanosecond", n[n.IntervalMonthDayNano = -31] = "IntervalMonthDayNano";
})(l || (l = {}));
var Ut;
(function(n) {
  n[n.OFFSET = 0] = "OFFSET", n[n.DATA = 1] = "DATA", n[n.VALIDITY = 2] = "VALIDITY", n[n.TYPE = 3] = "TYPE";
})(Ut || (Ut = {}));
const Ca = void 0;
function ie(n) {
  if (n === null)
    return "null";
  if (n === Ca)
    return "undefined";
  switch (typeof n) {
    case "number":
      return `${n}`;
    case "bigint":
      return `${n}`;
    case "string":
      return `"${n}"`;
  }
  return typeof n[Symbol.toPrimitive] == "function" ? n[Symbol.toPrimitive]("string") : ArrayBuffer.isView(n) ? n instanceof BigInt64Array || n instanceof BigUint64Array ? `[${[...n].map((t) => ie(t))}]` : `[${n}]` : ArrayBuffer.isView(n) ? `[${n}]` : JSON.stringify(n, (t, e) => typeof e == "bigint" ? `${e}` : e);
}
function P(n) {
  if (typeof n == "bigint" && (n < Number.MIN_SAFE_INTEGER || n > Number.MAX_SAFE_INTEGER))
    throw new TypeError(`${n} is not safe to convert to a number.`);
  return Number(n);
}
function tr(n, t) {
  return P(n / t) + P(n % t) / P(t);
}
const Ea = /* @__PURE__ */ Symbol.for("isArrowBigNum");
function vt(n, ...t) {
  return t.length === 0 ? Object.setPrototypeOf(R(this.TypedArray, n), this.constructor.prototype) : Object.setPrototypeOf(new this.TypedArray(n, ...t), this.constructor.prototype);
}
vt.prototype[Ea] = !0;
vt.prototype.toJSON = function() {
  return `"${Ge(this)}"`;
};
vt.prototype.valueOf = function(n) {
  return er(this, n);
};
vt.prototype.toString = function() {
  return Ge(this);
};
vt.prototype[Symbol.toPrimitive] = function(n = "default") {
  switch (n) {
    case "number":
      return er(this);
    case "string":
      return Ge(this);
    case "default":
      return za(this);
  }
  return Ge(this);
};
function ve(...n) {
  return vt.apply(this, n);
}
function we(...n) {
  return vt.apply(this, n);
}
function Ke(...n) {
  return vt.apply(this, n);
}
Object.setPrototypeOf(ve.prototype, Object.create(Int32Array.prototype));
Object.setPrototypeOf(we.prototype, Object.create(Uint32Array.prototype));
Object.setPrototypeOf(Ke.prototype, Object.create(Uint32Array.prototype));
Object.assign(ve.prototype, vt.prototype, { constructor: ve, signed: !0, TypedArray: Int32Array, BigIntArray: BigInt64Array });
Object.assign(we.prototype, vt.prototype, { constructor: we, signed: !1, TypedArray: Uint32Array, BigIntArray: BigUint64Array });
Object.assign(Ke.prototype, vt.prototype, { constructor: Ke, signed: !0, TypedArray: Uint32Array, BigIntArray: BigUint64Array });
const Va = BigInt(4294967296) * BigInt(4294967296), Ra = Va - BigInt(1);
function er(n, t) {
  const { buffer: e, byteOffset: i, byteLength: s, signed: r } = n, o = new BigUint64Array(e, i, s / 8), a = r && o.at(-1) & BigInt(1) << BigInt(63);
  let c = BigInt(0), u = 0;
  if (a) {
    for (const d of o)
      c |= (d ^ Ra) * (BigInt(1) << BigInt(64 * u++));
    c *= BigInt(-1), c -= BigInt(1);
  } else
    for (const d of o)
      c |= d * (BigInt(1) << BigInt(64 * u++));
  if (typeof t == "number" && t > 0) {
    const d = BigInt("1".padEnd(t + 1, "0")), h = c / d, N = a ? -(c % d) : c % d, B = P(h), z = `${N}`.padStart(t, "0");
    return +`${a && B === 0 ? "-" : ""}${B}.${z}`;
  }
  return P(c);
}
function Ge(n) {
  if (n.byteLength === 8)
    return `${new n.BigIntArray(n.buffer, n.byteOffset, 1)[0]}`;
  if (!n.signed)
    return si(n);
  let t = new Uint16Array(n.buffer, n.byteOffset, n.byteLength / 2);
  if (new Int16Array([t.at(-1)])[0] >= 0)
    return si(n);
  t = t.slice();
  let i = 1;
  for (let r = 0; r < t.length; r++) {
    const o = t[r], a = ~o + i;
    t[r] = a, i &= o === 0 ? 1 : 0;
  }
  return `-${si(t)}`;
}
function za(n) {
  return n.byteLength === 8 ? new n.BigIntArray(n.buffer, n.byteOffset, 1)[0] : Ge(n);
}
function si(n) {
  let t = "";
  const e = new Uint32Array(2);
  let i = new Uint16Array(n.buffer, n.byteOffset, n.byteLength / 2);
  const s = new Uint32Array((i = new Uint16Array(i).reverse()).buffer);
  let r = -1;
  const o = i.length - 1;
  do {
    for (e[0] = i[r = 0]; r < o; )
      i[r++] = e[1] = e[0] / 10, e[0] = (e[0] - e[1] * 10 << 16) + i[r];
    i[r] = e[1] = e[0] / 10, e[0] = e[0] - e[1] * 10, t = `${e[0]}${t}`;
  } while (s[0] || s[1] || s[2] || s[3]);
  return t ?? "0";
}
class Oi {
  /** @nocollapse */
  static new(t, e) {
    switch (e) {
      case !0:
        return new ve(t);
      case !1:
        return new we(t);
    }
    switch (t.constructor) {
      case Int8Array:
      case Int16Array:
      case Int32Array:
      case BigInt64Array:
        return new ve(t);
    }
    return t.byteLength === 16 ? new Ke(t) : new we(t);
  }
  /** @nocollapse */
  static signed(t) {
    return new ve(t);
  }
  /** @nocollapse */
  static unsigned(t) {
    return new we(t);
  }
  /** @nocollapse */
  static decimal(t) {
    return new Ke(t);
  }
  constructor(t, e) {
    return Oi.new(t, e);
  }
}
var nr, ir, sr, rr, or, ar, cr, lr, ur, dr, hr, fr, pr, yr, gr, mr, br, _r, vr, wr, Ir, Sr;
class f {
  /** @nocollapse */
  static isNull(t) {
    return t?.typeId === l.Null;
  }
  /** @nocollapse */
  static isInt(t) {
    return t?.typeId === l.Int;
  }
  /** @nocollapse */
  static isFloat(t) {
    return t?.typeId === l.Float;
  }
  /** @nocollapse */
  static isBinary(t) {
    return t?.typeId === l.Binary;
  }
  /** @nocollapse */
  static isLargeBinary(t) {
    return t?.typeId === l.LargeBinary;
  }
  /** @nocollapse */
  static isUtf8(t) {
    return t?.typeId === l.Utf8;
  }
  /** @nocollapse */
  static isLargeUtf8(t) {
    return t?.typeId === l.LargeUtf8;
  }
  /** @nocollapse */
  static isBool(t) {
    return t?.typeId === l.Bool;
  }
  /** @nocollapse */
  static isDecimal(t) {
    return t?.typeId === l.Decimal;
  }
  /** @nocollapse */
  static isDate(t) {
    return t?.typeId === l.Date;
  }
  /** @nocollapse */
  static isTime(t) {
    return t?.typeId === l.Time;
  }
  /** @nocollapse */
  static isTimestamp(t) {
    return t?.typeId === l.Timestamp;
  }
  /** @nocollapse */
  static isInterval(t) {
    return t?.typeId === l.Interval;
  }
  /** @nocollapse */
  static isDuration(t) {
    return t?.typeId === l.Duration;
  }
  /** @nocollapse */
  static isList(t) {
    return t?.typeId === l.List;
  }
  /** @nocollapse */
  static isStruct(t) {
    return t?.typeId === l.Struct;
  }
  /** @nocollapse */
  static isUnion(t) {
    return t?.typeId === l.Union;
  }
  /** @nocollapse */
  static isFixedSizeBinary(t) {
    return t?.typeId === l.FixedSizeBinary;
  }
  /** @nocollapse */
  static isFixedSizeList(t) {
    return t?.typeId === l.FixedSizeList;
  }
  /** @nocollapse */
  static isMap(t) {
    return t?.typeId === l.Map;
  }
  /** @nocollapse */
  static isDictionary(t) {
    return t?.typeId === l.Dictionary;
  }
  /** @nocollapse */
  static isDenseUnion(t) {
    return f.isUnion(t) && t.mode === tt.Dense;
  }
  /** @nocollapse */
  static isSparseUnion(t) {
    return f.isUnion(t) && t.mode === tt.Sparse;
  }
  constructor(t) {
    this.typeId = t;
  }
}
nr = Symbol.toStringTag;
f[nr] = ((n) => (n.children = null, n.ArrayType = Array, n.OffsetArrayType = Int32Array, n[Symbol.toStringTag] = "DataType"))(f.prototype);
class Rt extends f {
  constructor() {
    super(l.Null);
  }
  toString() {
    return "Null";
  }
}
ir = Symbol.toStringTag;
Rt[ir] = ((n) => n[Symbol.toStringTag] = "Null")(Rt.prototype);
class et extends f {
  constructor(t, e) {
    super(l.Int), this.isSigned = t, this.bitWidth = e;
  }
  get ArrayType() {
    switch (this.bitWidth) {
      case 8:
        return this.isSigned ? Int8Array : Uint8Array;
      case 16:
        return this.isSigned ? Int16Array : Uint16Array;
      case 32:
        return this.isSigned ? Int32Array : Uint32Array;
      case 64:
        return this.isSigned ? BigInt64Array : BigUint64Array;
    }
    throw new Error(`Unrecognized ${this[Symbol.toStringTag]} type`);
  }
  toString() {
    return `${this.isSigned ? "I" : "Ui"}nt${this.bitWidth}`;
  }
}
sr = Symbol.toStringTag;
et[sr] = ((n) => (n.isSigned = null, n.bitWidth = null, n[Symbol.toStringTag] = "Int"))(et.prototype);
class Br extends et {
  constructor() {
    super(!0, 8);
  }
  get ArrayType() {
    return Int8Array;
  }
}
class Ar extends et {
  constructor() {
    super(!0, 16);
  }
  get ArrayType() {
    return Int16Array;
  }
}
class Jt extends et {
  constructor() {
    super(!0, 32);
  }
  get ArrayType() {
    return Int32Array;
  }
}
let Fi = class extends et {
  constructor() {
    super(!0, 64);
  }
  get ArrayType() {
    return BigInt64Array;
  }
};
class Dr extends et {
  constructor() {
    super(!1, 8);
  }
  get ArrayType() {
    return Uint8Array;
  }
}
class Or extends et {
  constructor() {
    super(!1, 16);
  }
  get ArrayType() {
    return Uint16Array;
  }
}
class Fr extends et {
  constructor() {
    super(!1, 32);
  }
  get ArrayType() {
    return Uint32Array;
  }
}
let Mr = class extends et {
  constructor() {
    super(!1, 64);
  }
  get ArrayType() {
    return BigUint64Array;
  }
};
Object.defineProperty(Br.prototype, "ArrayType", { value: Int8Array });
Object.defineProperty(Ar.prototype, "ArrayType", { value: Int16Array });
Object.defineProperty(Jt.prototype, "ArrayType", { value: Int32Array });
Object.defineProperty(Fi.prototype, "ArrayType", { value: BigInt64Array });
Object.defineProperty(Dr.prototype, "ArrayType", { value: Uint8Array });
Object.defineProperty(Or.prototype, "ArrayType", { value: Uint16Array });
Object.defineProperty(Fr.prototype, "ArrayType", { value: Uint32Array });
Object.defineProperty(Mr.prototype, "ArrayType", { value: BigUint64Array });
class Ae extends f {
  constructor(t) {
    super(l.Float), this.precision = t;
  }
  get ArrayType() {
    switch (this.precision) {
      case W.HALF:
        return Uint16Array;
      case W.SINGLE:
        return Float32Array;
      case W.DOUBLE:
        return Float64Array;
    }
    throw new Error(`Unrecognized ${this[Symbol.toStringTag]} type`);
  }
  toString() {
    return `Float${this.precision << 5 || 16}`;
  }
}
rr = Symbol.toStringTag;
Ae[rr] = ((n) => (n.precision = null, n[Symbol.toStringTag] = "Float"))(Ae.prototype);
class Nr extends Ae {
  constructor() {
    super(W.SINGLE);
  }
}
class Jn extends Ae {
  constructor() {
    super(W.DOUBLE);
  }
}
Object.defineProperty(Nr.prototype, "ArrayType", { value: Float32Array });
Object.defineProperty(Jn.prototype, "ArrayType", { value: Float64Array });
class On extends f {
  constructor() {
    super(l.Binary);
  }
  toString() {
    return "Binary";
  }
}
or = Symbol.toStringTag;
On[or] = ((n) => (n.ArrayType = Uint8Array, n[Symbol.toStringTag] = "Binary"))(On.prototype);
class Fn extends f {
  constructor() {
    super(l.LargeBinary);
  }
  toString() {
    return "LargeBinary";
  }
}
ar = Symbol.toStringTag;
Fn[ar] = ((n) => (n.ArrayType = Uint8Array, n.OffsetArrayType = BigInt64Array, n[Symbol.toStringTag] = "LargeBinary"))(Fn.prototype);
class Ze extends f {
  constructor() {
    super(l.Utf8);
  }
  toString() {
    return "Utf8";
  }
}
cr = Symbol.toStringTag;
Ze[cr] = ((n) => (n.ArrayType = Uint8Array, n[Symbol.toStringTag] = "Utf8"))(Ze.prototype);
class Mn extends f {
  constructor() {
    super(l.LargeUtf8);
  }
  toString() {
    return "LargeUtf8";
  }
}
lr = Symbol.toStringTag;
Mn[lr] = ((n) => (n.ArrayType = Uint8Array, n.OffsetArrayType = BigInt64Array, n[Symbol.toStringTag] = "LargeUtf8"))(Mn.prototype);
class Qe extends f {
  constructor() {
    super(l.Bool);
  }
  toString() {
    return "Bool";
  }
}
ur = Symbol.toStringTag;
Qe[ur] = ((n) => (n.ArrayType = Uint8Array, n[Symbol.toStringTag] = "Bool"))(Qe.prototype);
class Nn extends f {
  constructor(t, e, i = 128) {
    super(l.Decimal), this.scale = t, this.precision = e, this.bitWidth = i;
  }
  toString() {
    return `Decimal[${this.precision}e${this.scale > 0 ? "+" : ""}${this.scale}]`;
  }
}
dr = Symbol.toStringTag;
Nn[dr] = ((n) => (n.scale = null, n.precision = null, n.ArrayType = Uint32Array, n[Symbol.toStringTag] = "Decimal"))(Nn.prototype);
class Tn extends f {
  constructor(t) {
    super(l.Date), this.unit = t;
  }
  toString() {
    return `Date${(this.unit + 1) * 32}<${ft[this.unit]}>`;
  }
  get ArrayType() {
    return this.unit === ft.DAY ? Int32Array : BigInt64Array;
  }
}
hr = Symbol.toStringTag;
Tn[hr] = ((n) => (n.unit = null, n[Symbol.toStringTag] = "Date"))(Tn.prototype);
class Ln extends f {
  constructor(t, e) {
    super(l.Time), this.unit = t, this.bitWidth = e;
  }
  toString() {
    return `Time${this.bitWidth}<${b[this.unit]}>`;
  }
  get ArrayType() {
    switch (this.bitWidth) {
      case 32:
        return Int32Array;
      case 64:
        return BigInt64Array;
    }
    throw new Error(`Unrecognized ${this[Symbol.toStringTag]} type`);
  }
}
fr = Symbol.toStringTag;
Ln[fr] = ((n) => (n.unit = null, n.bitWidth = null, n[Symbol.toStringTag] = "Time"))(Ln.prototype);
class Xe extends f {
  constructor(t, e) {
    super(l.Timestamp), this.unit = t, this.timezone = e;
  }
  toString() {
    return `Timestamp<${b[this.unit]}${this.timezone ? `, ${this.timezone}` : ""}>`;
  }
}
pr = Symbol.toStringTag;
Xe[pr] = ((n) => (n.unit = null, n.timezone = null, n.ArrayType = BigInt64Array, n[Symbol.toStringTag] = "Timestamp"))(Xe.prototype);
class ka extends Xe {
  constructor(t) {
    super(b.MILLISECOND, t);
  }
}
class xn extends f {
  constructor(t) {
    super(l.Interval), this.unit = t;
  }
  toString() {
    return `Interval<${J[this.unit]}>`;
  }
}
yr = Symbol.toStringTag;
xn[yr] = ((n) => (n.unit = null, n.ArrayType = Int32Array, n[Symbol.toStringTag] = "Interval"))(xn.prototype);
class Un extends f {
  constructor(t) {
    super(l.Duration), this.unit = t;
  }
  toString() {
    return `Duration<${b[this.unit]}>`;
  }
}
gr = Symbol.toStringTag;
Un[gr] = ((n) => (n.unit = null, n.ArrayType = BigInt64Array, n[Symbol.toStringTag] = "Duration"))(Un.prototype);
class De extends f {
  constructor(t) {
    super(l.List), this.children = [t];
  }
  toString() {
    return `List<${this.valueType}>`;
  }
  get valueType() {
    return this.children[0].type;
  }
  get valueField() {
    return this.children[0];
  }
  get ArrayType() {
    return this.valueType.ArrayType;
  }
}
mr = Symbol.toStringTag;
De[mr] = ((n) => (n.children = null, n[Symbol.toStringTag] = "List"))(De.prototype);
class q extends f {
  constructor(t) {
    super(l.Struct), this.children = t;
  }
  toString() {
    return `Struct<{${this.children.map((t) => `${t.name}:${t.type}`).join(", ")}}>`;
  }
}
br = Symbol.toStringTag;
q[br] = ((n) => (n.children = null, n[Symbol.toStringTag] = "Struct"))(q.prototype);
class tn extends f {
  constructor(t, e, i) {
    super(l.Union), this.mode = t, this.children = i, this.typeIds = e = Int32Array.from(e), this.typeIdToChildIndex = e.reduce((s, r, o) => (s[r] = o) && s || s, /* @__PURE__ */ Object.create(null));
  }
  toString() {
    return `${this[Symbol.toStringTag]}<${this.children.map((t) => `${t.type}`).join(" | ")}>`;
  }
}
_r = Symbol.toStringTag;
tn[_r] = ((n) => (n.mode = null, n.typeIds = null, n.children = null, n.typeIdToChildIndex = null, n.ArrayType = Int8Array, n[Symbol.toStringTag] = "Union"))(tn.prototype);
class Cn extends f {
  constructor(t) {
    super(l.FixedSizeBinary), this.byteWidth = t;
  }
  toString() {
    return `FixedSizeBinary[${this.byteWidth}]`;
  }
}
vr = Symbol.toStringTag;
Cn[vr] = ((n) => (n.byteWidth = null, n.ArrayType = Uint8Array, n[Symbol.toStringTag] = "FixedSizeBinary"))(Cn.prototype);
class en extends f {
  constructor(t, e) {
    super(l.FixedSizeList), this.listSize = t, this.children = [e];
  }
  get valueType() {
    return this.children[0].type;
  }
  get valueField() {
    return this.children[0];
  }
  get ArrayType() {
    return this.valueType.ArrayType;
  }
  toString() {
    return `FixedSizeList[${this.listSize}]<${this.valueType}>`;
  }
}
wr = Symbol.toStringTag;
en[wr] = ((n) => (n.children = null, n.listSize = null, n[Symbol.toStringTag] = "FixedSizeList"))(en.prototype);
class nn extends f {
  constructor(t, e = !1) {
    var i, s, r;
    if (super(l.Map), this.children = [t], this.keysSorted = e, t && (t.name = "entries", !((i = t?.type) === null || i === void 0) && i.children)) {
      const o = (s = t?.type) === null || s === void 0 ? void 0 : s.children[0];
      o && (o.name = "key");
      const a = (r = t?.type) === null || r === void 0 ? void 0 : r.children[1];
      a && (a.name = "value");
    }
  }
  get keyType() {
    return this.children[0].type.children[0].type;
  }
  get valueType() {
    return this.children[0].type.children[1].type;
  }
  get childType() {
    return this.children[0].type;
  }
  toString() {
    return `Map<{${this.children[0].type.children.map((t) => `${t.name}:${t.type}`).join(", ")}}>`;
  }
}
Ir = Symbol.toStringTag;
nn[Ir] = ((n) => (n.children = null, n.keysSorted = null, n[Symbol.toStringTag] = "Map_"))(nn.prototype);
const Pa = /* @__PURE__ */ ((n) => () => ++n)(-1);
class Kt extends f {
  constructor(t, e, i, s) {
    super(l.Dictionary), this.indices = e, this.dictionary = t, this.isOrdered = s || !1, this.id = i == null ? Pa() : P(i);
  }
  get children() {
    return this.dictionary.children;
  }
  get valueType() {
    return this.dictionary;
  }
  get ArrayType() {
    return this.dictionary.ArrayType;
  }
  toString() {
    return `Dictionary<${this.indices}, ${this.dictionary}>`;
  }
}
Sr = Symbol.toStringTag;
Kt[Sr] = ((n) => (n.id = null, n.indices = null, n.isOrdered = null, n.dictionary = null, n[Symbol.toStringTag] = "Dictionary"))(Kt.prototype);
function Dt(n) {
  const t = n;
  switch (n.typeId) {
    case l.Decimal:
      return n.bitWidth / 32;
    case l.Interval:
      return t.unit === J.MONTH_DAY_NANO ? 4 : 1 + t.unit;
    // case Type.Int: return 1 + +((t as Int_).bitWidth > 32);
    // case Type.Time: return 1 + +((t as Time_).bitWidth > 32);
    case l.FixedSizeList:
      return t.listSize;
    case l.FixedSizeBinary:
      return t.byteWidth;
    default:
      return 1;
  }
}
class M {
  visitMany(t, ...e) {
    return t.map((i, s) => this.visit(i, ...e.map((r) => r[s])));
  }
  visit(...t) {
    return this.getVisitFn(t[0], !1).apply(this, t);
  }
  getVisitFn(t, e = !0) {
    return ja(this, t, e);
  }
  getVisitFnByTypeId(t, e = !0) {
    return pe(this, t, e);
  }
  visitNull(t, ...e) {
    return null;
  }
  visitBool(t, ...e) {
    return null;
  }
  visitInt(t, ...e) {
    return null;
  }
  visitFloat(t, ...e) {
    return null;
  }
  visitUtf8(t, ...e) {
    return null;
  }
  visitLargeUtf8(t, ...e) {
    return null;
  }
  visitBinary(t, ...e) {
    return null;
  }
  visitLargeBinary(t, ...e) {
    return null;
  }
  visitFixedSizeBinary(t, ...e) {
    return null;
  }
  visitDate(t, ...e) {
    return null;
  }
  visitTimestamp(t, ...e) {
    return null;
  }
  visitTime(t, ...e) {
    return null;
  }
  visitDecimal(t, ...e) {
    return null;
  }
  visitList(t, ...e) {
    return null;
  }
  visitStruct(t, ...e) {
    return null;
  }
  visitUnion(t, ...e) {
    return null;
  }
  visitDictionary(t, ...e) {
    return null;
  }
  visitInterval(t, ...e) {
    return null;
  }
  visitDuration(t, ...e) {
    return null;
  }
  visitFixedSizeList(t, ...e) {
    return null;
  }
  visitMap(t, ...e) {
    return null;
  }
}
function ja(n, t, e = !0) {
  return typeof t == "number" ? pe(n, t, e) : typeof t == "string" && t in l ? pe(n, l[t], e) : t && t instanceof f ? pe(n, Ss(t), e) : t?.type && t.type instanceof f ? pe(n, Ss(t.type), e) : pe(n, l.NONE, e);
}
function pe(n, t, e = !0) {
  let i = null;
  switch (t) {
    case l.Null:
      i = n.visitNull;
      break;
    case l.Bool:
      i = n.visitBool;
      break;
    case l.Int:
      i = n.visitInt;
      break;
    case l.Int8:
      i = n.visitInt8 || n.visitInt;
      break;
    case l.Int16:
      i = n.visitInt16 || n.visitInt;
      break;
    case l.Int32:
      i = n.visitInt32 || n.visitInt;
      break;
    case l.Int64:
      i = n.visitInt64 || n.visitInt;
      break;
    case l.Uint8:
      i = n.visitUint8 || n.visitInt;
      break;
    case l.Uint16:
      i = n.visitUint16 || n.visitInt;
      break;
    case l.Uint32:
      i = n.visitUint32 || n.visitInt;
      break;
    case l.Uint64:
      i = n.visitUint64 || n.visitInt;
      break;
    case l.Float:
      i = n.visitFloat;
      break;
    case l.Float16:
      i = n.visitFloat16 || n.visitFloat;
      break;
    case l.Float32:
      i = n.visitFloat32 || n.visitFloat;
      break;
    case l.Float64:
      i = n.visitFloat64 || n.visitFloat;
      break;
    case l.Utf8:
      i = n.visitUtf8;
      break;
    case l.LargeUtf8:
      i = n.visitLargeUtf8;
      break;
    case l.Binary:
      i = n.visitBinary;
      break;
    case l.LargeBinary:
      i = n.visitLargeBinary;
      break;
    case l.FixedSizeBinary:
      i = n.visitFixedSizeBinary;
      break;
    case l.Date:
      i = n.visitDate;
      break;
    case l.DateDay:
      i = n.visitDateDay || n.visitDate;
      break;
    case l.DateMillisecond:
      i = n.visitDateMillisecond || n.visitDate;
      break;
    case l.Timestamp:
      i = n.visitTimestamp;
      break;
    case l.TimestampSecond:
      i = n.visitTimestampSecond || n.visitTimestamp;
      break;
    case l.TimestampMillisecond:
      i = n.visitTimestampMillisecond || n.visitTimestamp;
      break;
    case l.TimestampMicrosecond:
      i = n.visitTimestampMicrosecond || n.visitTimestamp;
      break;
    case l.TimestampNanosecond:
      i = n.visitTimestampNanosecond || n.visitTimestamp;
      break;
    case l.Time:
      i = n.visitTime;
      break;
    case l.TimeSecond:
      i = n.visitTimeSecond || n.visitTime;
      break;
    case l.TimeMillisecond:
      i = n.visitTimeMillisecond || n.visitTime;
      break;
    case l.TimeMicrosecond:
      i = n.visitTimeMicrosecond || n.visitTime;
      break;
    case l.TimeNanosecond:
      i = n.visitTimeNanosecond || n.visitTime;
      break;
    case l.Decimal:
      i = n.visitDecimal;
      break;
    case l.List:
      i = n.visitList;
      break;
    case l.Struct:
      i = n.visitStruct;
      break;
    case l.Union:
      i = n.visitUnion;
      break;
    case l.DenseUnion:
      i = n.visitDenseUnion || n.visitUnion;
      break;
    case l.SparseUnion:
      i = n.visitSparseUnion || n.visitUnion;
      break;
    case l.Dictionary:
      i = n.visitDictionary;
      break;
    case l.Interval:
      i = n.visitInterval;
      break;
    case l.IntervalDayTime:
      i = n.visitIntervalDayTime || n.visitInterval;
      break;
    case l.IntervalYearMonth:
      i = n.visitIntervalYearMonth || n.visitInterval;
      break;
    case l.IntervalMonthDayNano:
      i = n.visitIntervalMonthDayNano || n.visitInterval;
      break;
    case l.Duration:
      i = n.visitDuration;
      break;
    case l.DurationSecond:
      i = n.visitDurationSecond || n.visitDuration;
      break;
    case l.DurationMillisecond:
      i = n.visitDurationMillisecond || n.visitDuration;
      break;
    case l.DurationMicrosecond:
      i = n.visitDurationMicrosecond || n.visitDuration;
      break;
    case l.DurationNanosecond:
      i = n.visitDurationNanosecond || n.visitDuration;
      break;
    case l.FixedSizeList:
      i = n.visitFixedSizeList;
      break;
    case l.Map:
      i = n.visitMap;
      break;
  }
  if (typeof i == "function")
    return i;
  if (!e)
    return () => null;
  throw new Error(`Unrecognized type '${l[t]}'`);
}
function Ss(n) {
  switch (n.typeId) {
    case l.Null:
      return l.Null;
    case l.Int: {
      const { bitWidth: t, isSigned: e } = n;
      switch (t) {
        case 8:
          return e ? l.Int8 : l.Uint8;
        case 16:
          return e ? l.Int16 : l.Uint16;
        case 32:
          return e ? l.Int32 : l.Uint32;
        case 64:
          return e ? l.Int64 : l.Uint64;
      }
      return l.Int;
    }
    case l.Float:
      switch (n.precision) {
        case W.HALF:
          return l.Float16;
        case W.SINGLE:
          return l.Float32;
        case W.DOUBLE:
          return l.Float64;
      }
      return l.Float;
    case l.Binary:
      return l.Binary;
    case l.LargeBinary:
      return l.LargeBinary;
    case l.Utf8:
      return l.Utf8;
    case l.LargeUtf8:
      return l.LargeUtf8;
    case l.Bool:
      return l.Bool;
    case l.Decimal:
      return l.Decimal;
    case l.Time:
      switch (n.unit) {
        case b.SECOND:
          return l.TimeSecond;
        case b.MILLISECOND:
          return l.TimeMillisecond;
        case b.MICROSECOND:
          return l.TimeMicrosecond;
        case b.NANOSECOND:
          return l.TimeNanosecond;
      }
      return l.Time;
    case l.Timestamp:
      switch (n.unit) {
        case b.SECOND:
          return l.TimestampSecond;
        case b.MILLISECOND:
          return l.TimestampMillisecond;
        case b.MICROSECOND:
          return l.TimestampMicrosecond;
        case b.NANOSECOND:
          return l.TimestampNanosecond;
      }
      return l.Timestamp;
    case l.Date:
      switch (n.unit) {
        case ft.DAY:
          return l.DateDay;
        case ft.MILLISECOND:
          return l.DateMillisecond;
      }
      return l.Date;
    case l.Interval:
      switch (n.unit) {
        case J.DAY_TIME:
          return l.IntervalDayTime;
        case J.YEAR_MONTH:
          return l.IntervalYearMonth;
        case J.MONTH_DAY_NANO:
          return l.IntervalMonthDayNano;
      }
      return l.Interval;
    case l.Duration:
      switch (n.unit) {
        case b.SECOND:
          return l.DurationSecond;
        case b.MILLISECOND:
          return l.DurationMillisecond;
        case b.MICROSECOND:
          return l.DurationMicrosecond;
        case b.NANOSECOND:
          return l.DurationNanosecond;
      }
      return l.Duration;
    case l.Map:
      return l.Map;
    case l.List:
      return l.List;
    case l.Struct:
      return l.Struct;
    case l.Union:
      switch (n.mode) {
        case tt.Dense:
          return l.DenseUnion;
        case tt.Sparse:
          return l.SparseUnion;
      }
      return l.Union;
    case l.FixedSizeBinary:
      return l.FixedSizeBinary;
    case l.FixedSizeList:
      return l.FixedSizeList;
    case l.Dictionary:
      return l.Dictionary;
  }
  throw new Error(`Unrecognized type '${l[n.typeId]}'`);
}
M.prototype.visitInt8 = null;
M.prototype.visitInt16 = null;
M.prototype.visitInt32 = null;
M.prototype.visitInt64 = null;
M.prototype.visitUint8 = null;
M.prototype.visitUint16 = null;
M.prototype.visitUint32 = null;
M.prototype.visitUint64 = null;
M.prototype.visitFloat16 = null;
M.prototype.visitFloat32 = null;
M.prototype.visitFloat64 = null;
M.prototype.visitDateDay = null;
M.prototype.visitDateMillisecond = null;
M.prototype.visitTimestampSecond = null;
M.prototype.visitTimestampMillisecond = null;
M.prototype.visitTimestampMicrosecond = null;
M.prototype.visitTimestampNanosecond = null;
M.prototype.visitTimeSecond = null;
M.prototype.visitTimeMillisecond = null;
M.prototype.visitTimeMicrosecond = null;
M.prototype.visitTimeNanosecond = null;
M.prototype.visitDenseUnion = null;
M.prototype.visitSparseUnion = null;
M.prototype.visitIntervalDayTime = null;
M.prototype.visitIntervalYearMonth = null;
M.prototype.visitIntervalMonthDayNano = null;
M.prototype.visitDuration = null;
M.prototype.visitDurationSecond = null;
M.prototype.visitDurationMillisecond = null;
M.prototype.visitDurationMicrosecond = null;
M.prototype.visitDurationNanosecond = null;
const Tr = new Float64Array(1), oe = new Uint32Array(Tr.buffer);
function Lr(n) {
  const t = (n & 31744) >> 10, e = (n & 1023) / 1024, i = Math.pow(-1, (n & 32768) >> 15);
  switch (t) {
    case 31:
      return i * (e ? Number.NaN : 1 / 0);
    case 0:
      return i * (e ? 6103515625e-14 * e : 0);
  }
  return i * Math.pow(2, t - 15) * (1 + e);
}
function xr(n) {
  if (n !== n)
    return 32256;
  Tr[0] = n;
  const t = (oe[1] & 2147483648) >> 16 & 65535;
  let e = oe[1] & 2146435072, i = 0;
  return e >= 1089470464 ? oe[0] > 0 ? e = 31744 : (e = (e & 2080374784) >> 16, i = (oe[1] & 1048575) >> 10) : e <= 1056964608 ? (i = 1048576 + (oe[1] & 1048575), i = 1048576 + (i << (e >> 20) - 998) >> 21, e = 0) : (e = e - 1056964608 >> 10, i = (oe[1] & 1048575) + 512 >> 10), t | e | i & 65535;
}
class _ extends M {
}
function S(n) {
  return (t, e, i) => {
    if (t.setValid(e, i != null))
      return n(t, e, i);
  };
}
const $a = (n, t, e) => {
  n[t] = Math.floor(e / 864e5);
}, Ur = (n, t, e, i) => {
  if (e + 1 < t.length) {
    const s = P(t[e]), r = P(t[e + 1]);
    n.set(i.subarray(0, r - s), s);
  }
}, Ya = ({ offset: n, values: t }, e, i) => {
  const s = n + e;
  i ? t[s >> 3] |= 1 << s % 8 : t[s >> 3] &= ~(1 << s % 8);
}, kt = ({ values: n }, t, e) => {
  n[t] = e;
}, Mi = ({ values: n }, t, e) => {
  n[t] = e;
}, Cr = ({ values: n }, t, e) => {
  n[t] = xr(e);
}, Ha = (n, t, e) => {
  switch (n.type.precision) {
    case W.HALF:
      return Cr(n, t, e);
    case W.SINGLE:
    case W.DOUBLE:
      return Mi(n, t, e);
  }
}, Ni = ({ values: n }, t, e) => {
  $a(n, t, e.valueOf());
}, Ti = ({ values: n }, t, e) => {
  n[t] = BigInt(e);
}, Er = ({ stride: n, values: t }, e, i) => {
  t.set(i.subarray(0, n), n * e);
}, Vr = ({ values: n, valueOffsets: t }, e, i) => Ur(n, t, e, i), Rr = ({ values: n, valueOffsets: t }, e, i) => Ur(n, t, e, sn(i)), zr = (n, t, e) => {
  n.type.unit === ft.DAY ? Ni(n, t, e) : Ti(n, t, e);
}, Li = ({ values: n }, t, e) => {
  n[t] = BigInt(e / 1e3);
}, xi = ({ values: n }, t, e) => {
  n[t] = BigInt(e);
}, Ui = ({ values: n }, t, e) => {
  n[t] = BigInt(e * 1e3);
}, Ci = ({ values: n }, t, e) => {
  n[t] = BigInt(e * 1e6);
}, kr = (n, t, e) => {
  switch (n.type.unit) {
    case b.SECOND:
      return Li(n, t, e);
    case b.MILLISECOND:
      return xi(n, t, e);
    case b.MICROSECOND:
      return Ui(n, t, e);
    case b.NANOSECOND:
      return Ci(n, t, e);
  }
}, Ei = ({ values: n }, t, e) => {
  n[t] = e;
}, Vi = ({ values: n }, t, e) => {
  n[t] = e;
}, Ri = ({ values: n }, t, e) => {
  n[t] = e;
}, zi = ({ values: n }, t, e) => {
  n[t] = e;
}, Pr = (n, t, e) => {
  switch (n.type.unit) {
    case b.SECOND:
      return Ei(n, t, e);
    case b.MILLISECOND:
      return Vi(n, t, e);
    case b.MICROSECOND:
      return Ri(n, t, e);
    case b.NANOSECOND:
      return zi(n, t, e);
  }
}, jr = ({ values: n, stride: t }, e, i) => {
  n.set(i.subarray(0, t), t * e);
}, Wa = (n, t, e) => {
  const i = n.children[0], s = n.valueOffsets, r = pt.getVisitFn(i);
  if (Array.isArray(e))
    for (let o = -1, a = s[t], c = s[t + 1]; a < c; )
      r(i, a++, e[++o]);
  else
    for (let o = -1, a = s[t], c = s[t + 1]; a < c; )
      r(i, a++, e.get(++o));
}, qa = (n, t, e) => {
  const i = n.children[0], { valueOffsets: s } = n, r = pt.getVisitFn(i);
  let { [t]: o, [t + 1]: a } = s;
  const c = e instanceof Map ? e.entries() : Object.entries(e);
  for (const u of c)
    if (r(i, o, u), ++o >= a)
      break;
}, Ja = (n, t) => (e, i, s, r) => i && e(i, n, t[r]), Ka = (n, t) => (e, i, s, r) => i && e(i, n, t.get(r)), Ga = (n, t) => (e, i, s, r) => i && e(i, n, t.get(s.name)), Za = (n, t) => (e, i, s, r) => i && e(i, n, t[s.name]), Qa = (n, t, e) => {
  const i = n.type.children.map((r) => pt.getVisitFn(r.type)), s = e instanceof Map ? Ga(t, e) : e instanceof D ? Ka(t, e) : Array.isArray(e) ? Ja(t, e) : Za(t, e);
  n.type.children.forEach((r, o) => s(i[o], n.children[o], r, o));
}, Xa = (n, t, e) => {
  n.type.mode === tt.Dense ? $r(n, t, e) : Yr(n, t, e);
}, $r = (n, t, e) => {
  const i = n.type.typeIdToChildIndex[n.typeIds[t]], s = n.children[i];
  pt.visit(s, n.valueOffsets[t], e);
}, Yr = (n, t, e) => {
  const i = n.type.typeIdToChildIndex[n.typeIds[t]], s = n.children[i];
  pt.visit(s, t, e);
}, tc = (n, t, e) => {
  var i;
  (i = n.dictionary) === null || i === void 0 || i.set(n.values[t], e);
}, Hr = (n, t, e) => {
  switch (n.type.unit) {
    case J.YEAR_MONTH:
      return Pi(n, t, e);
    case J.DAY_TIME:
      return ki(n, t, e);
    case J.MONTH_DAY_NANO:
      return ji(n, t, e);
  }
}, ki = ({ values: n }, t, e) => {
  n.set(e.subarray(0, 2), 2 * t);
}, Pi = ({ values: n }, t, e) => {
  n[t] = e[0] * 12 + e[1] % 12;
}, ji = ({ values: n, stride: t }, e, i) => {
  n.set(i.subarray(0, t), t * e);
}, $i = ({ values: n }, t, e) => {
  n[t] = e;
}, Yi = ({ values: n }, t, e) => {
  n[t] = e;
}, Hi = ({ values: n }, t, e) => {
  n[t] = e;
}, Wi = ({ values: n }, t, e) => {
  n[t] = e;
}, Wr = (n, t, e) => {
  switch (n.type.unit) {
    case b.SECOND:
      return $i(n, t, e);
    case b.MILLISECOND:
      return Yi(n, t, e);
    case b.MICROSECOND:
      return Hi(n, t, e);
    case b.NANOSECOND:
      return Wi(n, t, e);
  }
}, ec = (n, t, e) => {
  const { stride: i } = n, s = n.children[0], r = pt.getVisitFn(s);
  if (Array.isArray(e))
    for (let o = -1, a = t * i; ++o < i; )
      r(s, a + o, e[o]);
  else
    for (let o = -1, a = t * i; ++o < i; )
      r(s, a + o, e.get(o));
};
_.prototype.visitBool = S(Ya);
_.prototype.visitInt = S(kt);
_.prototype.visitInt8 = S(kt);
_.prototype.visitInt16 = S(kt);
_.prototype.visitInt32 = S(kt);
_.prototype.visitInt64 = S(kt);
_.prototype.visitUint8 = S(kt);
_.prototype.visitUint16 = S(kt);
_.prototype.visitUint32 = S(kt);
_.prototype.visitUint64 = S(kt);
_.prototype.visitFloat = S(Ha);
_.prototype.visitFloat16 = S(Cr);
_.prototype.visitFloat32 = S(Mi);
_.prototype.visitFloat64 = S(Mi);
_.prototype.visitUtf8 = S(Rr);
_.prototype.visitLargeUtf8 = S(Rr);
_.prototype.visitBinary = S(Vr);
_.prototype.visitLargeBinary = S(Vr);
_.prototype.visitFixedSizeBinary = S(Er);
_.prototype.visitDate = S(zr);
_.prototype.visitDateDay = S(Ni);
_.prototype.visitDateMillisecond = S(Ti);
_.prototype.visitTimestamp = S(kr);
_.prototype.visitTimestampSecond = S(Li);
_.prototype.visitTimestampMillisecond = S(xi);
_.prototype.visitTimestampMicrosecond = S(Ui);
_.prototype.visitTimestampNanosecond = S(Ci);
_.prototype.visitTime = S(Pr);
_.prototype.visitTimeSecond = S(Ei);
_.prototype.visitTimeMillisecond = S(Vi);
_.prototype.visitTimeMicrosecond = S(Ri);
_.prototype.visitTimeNanosecond = S(zi);
_.prototype.visitDecimal = S(jr);
_.prototype.visitList = S(Wa);
_.prototype.visitStruct = S(Qa);
_.prototype.visitUnion = S(Xa);
_.prototype.visitDenseUnion = S($r);
_.prototype.visitSparseUnion = S(Yr);
_.prototype.visitDictionary = S(tc);
_.prototype.visitInterval = S(Hr);
_.prototype.visitIntervalDayTime = S(ki);
_.prototype.visitIntervalYearMonth = S(Pi);
_.prototype.visitIntervalMonthDayNano = S(ji);
_.prototype.visitDuration = S(Wr);
_.prototype.visitDurationSecond = S($i);
_.prototype.visitDurationMillisecond = S(Yi);
_.prototype.visitDurationMicrosecond = S(Hi);
_.prototype.visitDurationNanosecond = S(Wi);
_.prototype.visitFixedSizeList = S(ec);
_.prototype.visitMap = S(qa);
const pt = new _(), gt = /* @__PURE__ */ Symbol.for("parent"), Ie = /* @__PURE__ */ Symbol.for("rowIndex");
class qi {
  constructor(t, e) {
    return this[gt] = t, this[Ie] = e, new Proxy(this, sc);
  }
  toArray() {
    return Object.values(this.toJSON());
  }
  toJSON() {
    const t = this[Ie], e = this[gt], i = e.type.children, s = {};
    for (let r = -1, o = i.length; ++r < o; )
      s[i[r].name] = nt.visit(e.children[r], t);
    return s;
  }
  toString() {
    return `{${[...this].map(([t, e]) => `${ie(t)}: ${ie(e)}`).join(", ")}}`;
  }
  [/* @__PURE__ */ Symbol.for("nodejs.util.inspect.custom")]() {
    return this.toString();
  }
  [Symbol.iterator]() {
    return new nc(this[gt], this[Ie]);
  }
}
class nc {
  constructor(t, e) {
    this.childIndex = 0, this.children = t.children, this.rowIndex = e, this.childFields = t.type.children, this.numChildren = this.childFields.length;
  }
  [Symbol.iterator]() {
    return this;
  }
  next() {
    const t = this.childIndex;
    return t < this.numChildren ? (this.childIndex = t + 1, {
      done: !1,
      value: [
        this.childFields[t].name,
        nt.visit(this.children[t], this.rowIndex)
      ]
    }) : { done: !0, value: null };
  }
}
Object.defineProperties(qi.prototype, {
  [Symbol.toStringTag]: { enumerable: !1, configurable: !1, value: "Row" },
  [gt]: { writable: !0, enumerable: !1, configurable: !1, value: null },
  [Ie]: { writable: !0, enumerable: !1, configurable: !1, value: -1 }
});
class ic {
  isExtensible() {
    return !1;
  }
  deleteProperty() {
    return !1;
  }
  preventExtensions() {
    return !0;
  }
  ownKeys(t) {
    return t[gt].type.children.map((e) => e.name);
  }
  has(t, e) {
    return t[gt].type.children.some((i) => i.name === e);
  }
  getOwnPropertyDescriptor(t, e) {
    if (t[gt].type.children.some((i) => i.name === e))
      return { writable: !0, enumerable: !0, configurable: !0 };
  }
  get(t, e) {
    if (Reflect.has(t, e))
      return t[e];
    const i = t[gt].type.children.findIndex((s) => s.name === e);
    if (i !== -1) {
      const s = nt.visit(t[gt].children[i], t[Ie]);
      return Reflect.set(t, e, s), s;
    }
  }
  set(t, e, i) {
    const s = t[gt].type.children.findIndex((r) => r.name === e);
    return s !== -1 ? (pt.visit(t[gt].children[s], t[Ie], i), Reflect.set(t, e, i)) : Reflect.has(t, e) || typeof e == "symbol" ? Reflect.set(t, e, i) : !1;
  }
}
const sc = new ic();
class p extends M {
}
function v(n) {
  return (t, e) => t.getValid(e) ? n(t, e) : null;
}
const rc = (n, t) => 864e5 * n[t], oc = (n, t) => null, qr = (n, t, e) => {
  if (e + 1 >= t.length)
    return null;
  const i = P(t[e]), s = P(t[e + 1]);
  return n.subarray(i, s);
}, ac = ({ offset: n, values: t }, e) => {
  const i = n + e;
  return (t[i >> 3] & 1 << i % 8) !== 0;
}, Jr = ({ values: n }, t) => rc(n, t), Kr = ({ values: n }, t) => P(n[t]), Gt = ({ stride: n, values: t }, e) => t[n * e], cc = ({ stride: n, values: t }, e) => Lr(t[n * e]), Gr = ({ values: n }, t) => n[t], lc = ({ stride: n, values: t }, e) => t.subarray(n * e, n * (e + 1)), Zr = ({ values: n, valueOffsets: t }, e) => qr(n, t, e), Qr = ({ values: n, valueOffsets: t }, e) => {
  const i = qr(n, t, e);
  return i !== null ? ui(i) : null;
}, uc = ({ values: n }, t) => n[t], dc = ({ type: n, values: t }, e) => n.precision !== W.HALF ? t[e] : Lr(t[e]), hc = (n, t) => n.type.unit === ft.DAY ? Jr(n, t) : Kr(n, t), Xr = ({ values: n }, t) => 1e3 * P(n[t]), to = ({ values: n }, t) => P(n[t]), eo = ({ values: n }, t) => tr(n[t], BigInt(1e3)), no = ({ values: n }, t) => tr(n[t], BigInt(1e6)), fc = (n, t) => {
  switch (n.type.unit) {
    case b.SECOND:
      return Xr(n, t);
    case b.MILLISECOND:
      return to(n, t);
    case b.MICROSECOND:
      return eo(n, t);
    case b.NANOSECOND:
      return no(n, t);
  }
}, io = ({ values: n }, t) => n[t], so = ({ values: n }, t) => n[t], ro = ({ values: n }, t) => n[t], oo = ({ values: n }, t) => n[t], pc = (n, t) => {
  switch (n.type.unit) {
    case b.SECOND:
      return io(n, t);
    case b.MILLISECOND:
      return so(n, t);
    case b.MICROSECOND:
      return ro(n, t);
    case b.NANOSECOND:
      return oo(n, t);
  }
}, yc = ({ values: n, stride: t }, e) => Oi.decimal(n.subarray(t * e, t * (e + 1))), gc = (n, t) => {
  const { valueOffsets: e, stride: i, children: s } = n, { [t * i]: r, [t * i + 1]: o } = e, c = s[0].slice(r, o - r);
  return new D([c]);
}, mc = (n, t) => {
  const { valueOffsets: e, children: i } = n, { [t]: s, [t + 1]: r } = e, o = i[0];
  return new Kn(o.slice(s, r - s));
}, bc = (n, t) => new qi(n, t), _c = (n, t) => n.type.mode === tt.Dense ? ao(n, t) : co(n, t), ao = (n, t) => {
  const e = n.type.typeIdToChildIndex[n.typeIds[t]], i = n.children[e];
  return nt.visit(i, n.valueOffsets[t]);
}, co = (n, t) => {
  const e = n.type.typeIdToChildIndex[n.typeIds[t]], i = n.children[e];
  return nt.visit(i, t);
}, vc = (n, t) => {
  var e;
  return (e = n.dictionary) === null || e === void 0 ? void 0 : e.get(n.values[t]);
}, wc = (n, t) => n.type.unit === J.MONTH_DAY_NANO ? ho(n, t) : n.type.unit === J.DAY_TIME ? lo(n, t) : uo(n, t), lo = ({ values: n }, t) => n.subarray(2 * t, 2 * (t + 1)), uo = ({ values: n }, t) => {
  const e = n[t], i = new Int32Array(2);
  return i[0] = Math.trunc(e / 12), i[1] = Math.trunc(e % 12), i;
}, ho = ({ values: n }, t) => n.subarray(4 * t, 4 * (t + 1)), fo = ({ values: n }, t) => n[t], po = ({ values: n }, t) => n[t], yo = ({ values: n }, t) => n[t], go = ({ values: n }, t) => n[t], Ic = (n, t) => {
  switch (n.type.unit) {
    case b.SECOND:
      return fo(n, t);
    case b.MILLISECOND:
      return po(n, t);
    case b.MICROSECOND:
      return yo(n, t);
    case b.NANOSECOND:
      return go(n, t);
  }
}, Sc = (n, t) => {
  const { stride: e, children: i } = n, r = i[0].slice(t * e, e);
  return new D([r]);
};
p.prototype.visitNull = v(oc);
p.prototype.visitBool = v(ac);
p.prototype.visitInt = v(uc);
p.prototype.visitInt8 = v(Gt);
p.prototype.visitInt16 = v(Gt);
p.prototype.visitInt32 = v(Gt);
p.prototype.visitInt64 = v(Gr);
p.prototype.visitUint8 = v(Gt);
p.prototype.visitUint16 = v(Gt);
p.prototype.visitUint32 = v(Gt);
p.prototype.visitUint64 = v(Gr);
p.prototype.visitFloat = v(dc);
p.prototype.visitFloat16 = v(cc);
p.prototype.visitFloat32 = v(Gt);
p.prototype.visitFloat64 = v(Gt);
p.prototype.visitUtf8 = v(Qr);
p.prototype.visitLargeUtf8 = v(Qr);
p.prototype.visitBinary = v(Zr);
p.prototype.visitLargeBinary = v(Zr);
p.prototype.visitFixedSizeBinary = v(lc);
p.prototype.visitDate = v(hc);
p.prototype.visitDateDay = v(Jr);
p.prototype.visitDateMillisecond = v(Kr);
p.prototype.visitTimestamp = v(fc);
p.prototype.visitTimestampSecond = v(Xr);
p.prototype.visitTimestampMillisecond = v(to);
p.prototype.visitTimestampMicrosecond = v(eo);
p.prototype.visitTimestampNanosecond = v(no);
p.prototype.visitTime = v(pc);
p.prototype.visitTimeSecond = v(io);
p.prototype.visitTimeMillisecond = v(so);
p.prototype.visitTimeMicrosecond = v(ro);
p.prototype.visitTimeNanosecond = v(oo);
p.prototype.visitDecimal = v(yc);
p.prototype.visitList = v(gc);
p.prototype.visitStruct = v(bc);
p.prototype.visitUnion = v(_c);
p.prototype.visitDenseUnion = v(ao);
p.prototype.visitSparseUnion = v(co);
p.prototype.visitDictionary = v(vc);
p.prototype.visitInterval = v(wc);
p.prototype.visitIntervalDayTime = v(lo);
p.prototype.visitIntervalYearMonth = v(uo);
p.prototype.visitIntervalMonthDayNano = v(ho);
p.prototype.visitDuration = v(Ic);
p.prototype.visitDurationSecond = v(fo);
p.prototype.visitDurationMillisecond = v(po);
p.prototype.visitDurationMicrosecond = v(yo);
p.prototype.visitDurationNanosecond = v(go);
p.prototype.visitFixedSizeList = v(Sc);
p.prototype.visitMap = v(mc);
const nt = new p(), Xt = /* @__PURE__ */ Symbol.for("keys"), Se = /* @__PURE__ */ Symbol.for("vals"), ye = /* @__PURE__ */ Symbol.for("kKeysAsStrings"), mi = /* @__PURE__ */ Symbol.for("_kKeysAsStrings");
class Kn {
  constructor(t) {
    return this[Xt] = new D([t.children[0]]).memoize(), this[Se] = t.children[1], new Proxy(this, new Ac());
  }
  /** @ignore */
  get [ye]() {
    return this[mi] || (this[mi] = Array.from(this[Xt].toArray(), String));
  }
  [Symbol.iterator]() {
    return new Bc(this[Xt], this[Se]);
  }
  get size() {
    return this[Xt].length;
  }
  toArray() {
    return Object.values(this.toJSON());
  }
  toJSON() {
    const t = this[Xt], e = this[Se], i = {};
    for (let s = -1, r = t.length; ++s < r; )
      i[t.get(s)] = nt.visit(e, s);
    return i;
  }
  toString() {
    return `{${[...this].map(([t, e]) => `${ie(t)}: ${ie(e)}`).join(", ")}}`;
  }
  [/* @__PURE__ */ Symbol.for("nodejs.util.inspect.custom")]() {
    return this.toString();
  }
}
class Bc {
  constructor(t, e) {
    this.keys = t, this.vals = e, this.keyIndex = 0, this.numKeys = t.length;
  }
  [Symbol.iterator]() {
    return this;
  }
  next() {
    const t = this.keyIndex;
    return t === this.numKeys ? { done: !0, value: null } : (this.keyIndex++, {
      done: !1,
      value: [
        this.keys.get(t),
        nt.visit(this.vals, t)
      ]
    });
  }
}
class Ac {
  isExtensible() {
    return !1;
  }
  deleteProperty() {
    return !1;
  }
  preventExtensions() {
    return !0;
  }
  ownKeys(t) {
    return t[ye];
  }
  has(t, e) {
    return t[ye].includes(e);
  }
  getOwnPropertyDescriptor(t, e) {
    if (t[ye].indexOf(e) !== -1)
      return { writable: !0, enumerable: !0, configurable: !0 };
  }
  get(t, e) {
    if (Reflect.has(t, e))
      return t[e];
    const i = t[ye].indexOf(e);
    if (i !== -1) {
      const s = nt.visit(Reflect.get(t, Se), i);
      return Reflect.set(t, e, s), s;
    }
  }
  set(t, e, i) {
    const s = t[ye].indexOf(e);
    return s !== -1 ? (pt.visit(Reflect.get(t, Se), s, i), Reflect.set(t, e, i)) : Reflect.has(t, e) ? Reflect.set(t, e, i) : !1;
  }
}
Object.defineProperties(Kn.prototype, {
  [Symbol.toStringTag]: { enumerable: !1, configurable: !1, value: "Row" },
  [Xt]: { writable: !0, enumerable: !1, configurable: !1, value: null },
  [Se]: { writable: !0, enumerable: !1, configurable: !1, value: null },
  [mi]: { writable: !0, enumerable: !1, configurable: !1, value: null }
});
let Bs;
function mo(n, t, e, i) {
  const { length: s = 0 } = n;
  let r = typeof t != "number" ? 0 : t, o = typeof e != "number" ? s : e;
  return r < 0 && (r = (r % s + s) % s), o < 0 && (o = (o % s + s) % s), o < r && (Bs = r, r = o, o = Bs), o > s && (o = s), i ? i(n, r, o) : [r, o];
}
const Ji = (n, t) => n < 0 ? t + n : n, As = (n) => n !== n;
function Ne(n) {
  if (typeof n !== "object" || n === null)
    return As(n) ? As : (e) => e === n;
  if (n instanceof Date) {
    const e = n.valueOf();
    return (i) => i instanceof Date ? i.valueOf() === e : !1;
  }
  return ArrayBuffer.isView(n) ? (e) => e ? Ma(n, e) : !1 : n instanceof Map ? Oc(n) : Array.isArray(n) ? Dc(n) : n instanceof D ? Fc(n) : Mc(n, !0);
}
function Dc(n) {
  const t = [];
  for (let e = -1, i = n.length; ++e < i; )
    t[e] = Ne(n[e]);
  return Gn(t);
}
function Oc(n) {
  let t = -1;
  const e = [];
  for (const i of n.values())
    e[++t] = Ne(i);
  return Gn(e);
}
function Fc(n) {
  const t = [];
  for (let e = -1, i = n.length; ++e < i; )
    t[e] = Ne(n.get(e));
  return Gn(t);
}
function Mc(n, t = !1) {
  const e = Object.keys(n);
  if (!t && e.length === 0)
    return () => !1;
  const i = [];
  for (let s = -1, r = e.length; ++s < r; )
    i[s] = Ne(n[e[s]]);
  return Gn(i, e);
}
function Gn(n, t) {
  return (e) => {
    if (!e || typeof e != "object")
      return !1;
    switch (e.constructor) {
      case Array:
        return Nc(n, e);
      case Map:
        return Ds(n, e, e.keys());
      case Kn:
      case qi:
      case Object:
      case void 0:
        return Ds(n, e, t || Object.keys(e));
    }
    return e instanceof D ? Tc(n, e) : !1;
  };
}
function Nc(n, t) {
  const e = n.length;
  if (t.length !== e)
    return !1;
  for (let i = -1; ++i < e; )
    if (!n[i](t[i]))
      return !1;
  return !0;
}
function Tc(n, t) {
  const e = n.length;
  if (t.length !== e)
    return !1;
  for (let i = -1; ++i < e; )
    if (!n[i](t.get(i)))
      return !1;
  return !0;
}
function Ds(n, t, e) {
  const i = e[Symbol.iterator](), s = t instanceof Map ? t.keys() : Object.keys(t)[Symbol.iterator](), r = t instanceof Map ? t.values() : Object.values(t)[Symbol.iterator]();
  let o = 0;
  const a = n.length;
  let c = r.next(), u = i.next(), d = s.next();
  for (; o < a && !u.done && !d.done && !c.done && !(u.value !== d.value || !n[o](c.value)); ++o, u = i.next(), d = s.next(), c = r.next())
    ;
  return o === a && u.done && d.done && c.done ? !0 : (i.return && i.return(), s.return && s.return(), r.return && r.return(), !1);
}
function bo(n, t, e, i) {
  return (e & 1 << i) !== 0;
}
function Lc(n, t, e, i) {
  return (e & 1 << i) >> i;
}
function Os(n, t, e) {
  const i = e.byteLength + 7 & -8;
  if (n > 0 || e.byteLength < i) {
    const s = new Uint8Array(i);
    return s.set(n % 8 === 0 ? e.subarray(n >> 3) : (
      // Otherwise iterate each bit from the offset and return a new one
      bi(new Ki(e, n, t, null, bo)).subarray(0, i)
    )), s;
  }
  return e;
}
function bi(n) {
  const t = [];
  let e = 0, i = 0, s = 0;
  for (const o of n)
    o && (s |= 1 << i), ++i === 8 && (t[e++] = s, s = i = 0);
  (e === 0 || i > 0) && (t[e++] = s);
  const r = new Uint8Array(t.length + 7 & -8);
  return r.set(t), r;
}
class Ki {
  constructor(t, e, i, s, r) {
    this.bytes = t, this.length = i, this.context = s, this.get = r, this.bit = e % 8, this.byteIndex = e >> 3, this.byte = t[this.byteIndex++], this.index = 0;
  }
  next() {
    return this.index < this.length ? (this.bit === 8 && (this.bit = 0, this.byte = this.bytes[this.byteIndex++]), {
      value: this.get(this.context, this.index++, this.byte, this.bit++)
    }) : { done: !0, value: null };
  }
  [Symbol.iterator]() {
    return this;
  }
}
function _i(n, t, e) {
  if (e - t <= 0)
    return 0;
  if (e - t < 8) {
    let r = 0;
    for (const o of new Ki(n, t, e - t, n, Lc))
      r += o;
    return r;
  }
  const i = e >> 3 << 3, s = t + (t % 8 === 0 ? 0 : 8 - t % 8);
  return (
    // Get the popcnt of bits between the left hand side, and the next highest multiple of 8
    _i(n, t, s) + // Get the popcnt of bits between the right hand side, and the next lowest multiple of 8
    _i(n, i, e) + // Get the popcnt of all bits between the left and right hand sides' multiples of 8
    xc(n, s >> 3, i - s >> 3)
  );
}
function xc(n, t, e) {
  let i = 0, s = Math.trunc(t);
  const r = new DataView(n.buffer, n.byteOffset, n.byteLength), o = e === void 0 ? n.byteLength : s + e;
  for (; o - s >= 4; )
    i += ri(r.getUint32(s)), s += 4;
  for (; o - s >= 2; )
    i += ri(r.getUint16(s)), s += 2;
  for (; o - s >= 1; )
    i += ri(r.getUint8(s)), s += 1;
  return i;
}
function ri(n) {
  let t = Math.trunc(n);
  return t = t - (t >>> 1 & 1431655765), t = (t & 858993459) + (t >>> 2 & 858993459), (t + (t >>> 4) & 252645135) * 16843009 >>> 24;
}
const Uc = -1;
class L {
  get typeId() {
    return this.type.typeId;
  }
  get ArrayType() {
    return this.type.ArrayType;
  }
  get buffers() {
    return [this.valueOffsets, this.values, this.nullBitmap, this.typeIds];
  }
  get nullable() {
    if (this._nullCount !== 0) {
      const { type: t } = this;
      return f.isSparseUnion(t) ? this.children.some((e) => e.nullable) : f.isDenseUnion(t) ? this.children.some((e) => e.nullable) : this.nullBitmap && this.nullBitmap.byteLength > 0;
    }
    return !0;
  }
  get byteLength() {
    let t = 0;
    const { valueOffsets: e, values: i, nullBitmap: s, typeIds: r } = this;
    return e && (t += e.byteLength), i && (t += i.byteLength), s && (t += s.byteLength), r && (t += r.byteLength), this.children.reduce((o, a) => o + a.byteLength, t);
  }
  get nullCount() {
    if (f.isUnion(this.type))
      return this.children.reduce((i, s) => i + s.nullCount, 0);
    let t = this._nullCount, e;
    return t <= Uc && (e = this.nullBitmap) && (this._nullCount = t = e.length === 0 ? (
      // no null bitmap, so all values are valid
      0
    ) : this.length - _i(e, this.offset, this.offset + this.length)), t;
  }
  constructor(t, e, i, s, r, o = [], a) {
    this.type = t, this.children = o, this.dictionary = a, this.offset = Math.floor(Math.max(e || 0, 0)), this.length = Math.floor(Math.max(i || 0, 0)), this._nullCount = Math.floor(Math.max(s || 0, -1));
    let c;
    r instanceof L ? (this.stride = r.stride, this.values = r.values, this.typeIds = r.typeIds, this.nullBitmap = r.nullBitmap, this.valueOffsets = r.valueOffsets) : (this.stride = Dt(t), r && ((c = r[0]) && (this.valueOffsets = c), (c = r[1]) && (this.values = c), (c = r[2]) && (this.nullBitmap = c), (c = r[3]) && (this.typeIds = c)));
  }
  getValid(t) {
    const { type: e } = this;
    if (f.isUnion(e)) {
      const i = e, s = this.children[i.typeIdToChildIndex[this.typeIds[t]]], r = i.mode === tt.Dense ? this.valueOffsets[t] : t;
      return s.getValid(r);
    }
    if (this.nullable && this.nullCount > 0) {
      const i = this.offset + t;
      return (this.nullBitmap[i >> 3] & 1 << i % 8) !== 0;
    }
    return !0;
  }
  setValid(t, e) {
    let i;
    const { type: s } = this;
    if (f.isUnion(s)) {
      const r = s, o = this.children[r.typeIdToChildIndex[this.typeIds[t]]], a = r.mode === tt.Dense ? this.valueOffsets[t] : t;
      i = o.getValid(a), o.setValid(a, e);
    } else {
      let { nullBitmap: r } = this;
      const { offset: o, length: a } = this, c = o + t, u = 1 << c % 8, d = c >> 3;
      (!r || r.byteLength <= d) && (r = new Uint8Array((o + a + 63 & -64) >> 3).fill(255), this.nullCount > 0 ? (r.set(Os(o, a, this.nullBitmap), 0), Object.assign(this, { nullBitmap: r })) : Object.assign(this, { nullBitmap: r, _nullCount: 0 }));
      const h = r[d];
      i = (h & u) !== 0, r[d] = e ? h | u : h & ~u;
    }
    return i !== !!e && (this._nullCount = this.nullCount + (e ? -1 : 1)), e;
  }
  clone(t = this.type, e = this.offset, i = this.length, s = this._nullCount, r = this, o = this.children) {
    return new L(t, e, i, s, r, o, this.dictionary);
  }
  slice(t, e) {
    const { stride: i, typeId: s, children: r } = this, o = +(this._nullCount === 0) - 1, a = s === 16 ? i : 1, c = this._sliceBuffers(t, e, i, s);
    return this.clone(
      this.type,
      this.offset + t,
      e,
      o,
      c,
      // Don't slice children if we have value offsets (the variable-width types)
      r.length === 0 || this.valueOffsets ? r : this._sliceChildren(r, a * t, a * e)
    );
  }
  _changeLengthAndBackfillNullBitmap(t) {
    if (this.typeId === l.Null)
      return this.clone(this.type, 0, t, 0);
    const { length: e, nullCount: i } = this, s = new Uint8Array((t + 63 & -64) >> 3).fill(255, 0, e >> 3);
    s[e >> 3] = (1 << e - (e & -8)) - 1, i > 0 && s.set(Os(this.offset, e, this.nullBitmap), 0);
    const r = this.buffers;
    return r[Ut.VALIDITY] = s, this.clone(this.type, 0, t, i + (t - e), r);
  }
  _sliceBuffers(t, e, i, s) {
    let r;
    const { buffers: o } = this;
    return (r = o[Ut.TYPE]) && (o[Ut.TYPE] = r.subarray(t, t + e)), (r = o[Ut.OFFSET]) && (o[Ut.OFFSET] = r.subarray(t, t + e + 1)) || // Otherwise if no offsets, slice the data buffer. Don't slice the data vector for Booleans, since the offset goes by bits not bytes
    (r = o[Ut.DATA]) && (o[Ut.DATA] = s === 6 ? r : r.subarray(i * t, i * (t + e))), o;
  }
  _sliceChildren(t, e, i) {
    return t.map((s) => s.slice(e, i));
  }
}
L.prototype.children = Object.freeze([]);
class We extends M {
  visit(t) {
    return this.getVisitFn(t.type).call(this, t);
  }
  visitNull(t) {
    const { ["type"]: e, ["offset"]: i = 0, ["length"]: s = 0 } = t;
    return new L(e, i, s, s);
  }
  visitBool(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = T(t.nullBitmap), r = R(e.ArrayType, t.data), { ["length"]: o = r.length >> 3, ["nullCount"]: a = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, o, a, [void 0, r, s]);
  }
  visitInt(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = T(t.nullBitmap), r = R(e.ArrayType, t.data), { ["length"]: o = r.length, ["nullCount"]: a = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, o, a, [void 0, r, s]);
  }
  visitFloat(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = T(t.nullBitmap), r = R(e.ArrayType, t.data), { ["length"]: o = r.length, ["nullCount"]: a = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, o, a, [void 0, r, s]);
  }
  visitUtf8(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = T(t.data), r = T(t.nullBitmap), o = Ve(t.valueOffsets), { ["length"]: a = o.length - 1, ["nullCount"]: c = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, a, c, [o, s, r]);
  }
  visitLargeUtf8(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = T(t.data), r = T(t.nullBitmap), o = fs(t.valueOffsets), { ["length"]: a = o.length - 1, ["nullCount"]: c = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, a, c, [o, s, r]);
  }
  visitBinary(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = T(t.data), r = T(t.nullBitmap), o = Ve(t.valueOffsets), { ["length"]: a = o.length - 1, ["nullCount"]: c = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, a, c, [o, s, r]);
  }
  visitLargeBinary(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = T(t.data), r = T(t.nullBitmap), o = fs(t.valueOffsets), { ["length"]: a = o.length - 1, ["nullCount"]: c = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, a, c, [o, s, r]);
  }
  visitFixedSizeBinary(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = T(t.nullBitmap), r = R(e.ArrayType, t.data), { ["length"]: o = r.length / Dt(e), ["nullCount"]: a = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, o, a, [void 0, r, s]);
  }
  visitDate(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = T(t.nullBitmap), r = R(e.ArrayType, t.data), { ["length"]: o = r.length / Dt(e), ["nullCount"]: a = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, o, a, [void 0, r, s]);
  }
  visitTimestamp(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = T(t.nullBitmap), r = R(e.ArrayType, t.data), { ["length"]: o = r.length / Dt(e), ["nullCount"]: a = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, o, a, [void 0, r, s]);
  }
  visitTime(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = T(t.nullBitmap), r = R(e.ArrayType, t.data), { ["length"]: o = r.length / Dt(e), ["nullCount"]: a = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, o, a, [void 0, r, s]);
  }
  visitDecimal(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = T(t.nullBitmap), r = R(e.ArrayType, t.data), { ["length"]: o = r.length / Dt(e), ["nullCount"]: a = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, o, a, [void 0, r, s]);
  }
  visitList(t) {
    const { ["type"]: e, ["offset"]: i = 0, ["child"]: s } = t, r = T(t.nullBitmap), o = Ve(t.valueOffsets), { ["length"]: a = o.length - 1, ["nullCount"]: c = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, a, c, [o, void 0, r], [s]);
  }
  visitStruct(t) {
    const { ["type"]: e, ["offset"]: i = 0, ["children"]: s = [] } = t, r = T(t.nullBitmap), { length: o = s.reduce((c, { length: u }) => Math.max(c, u), 0), nullCount: a = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, o, a, [void 0, void 0, r], s);
  }
  visitUnion(t) {
    const { ["type"]: e, ["offset"]: i = 0, ["children"]: s = [] } = t, r = R(e.ArrayType, t.typeIds), { ["length"]: o = r.length, ["nullCount"]: a = -1 } = t;
    if (f.isSparseUnion(e))
      return new L(e, i, o, a, [void 0, void 0, void 0, r], s);
    const c = Ve(t.valueOffsets);
    return new L(e, i, o, a, [c, void 0, void 0, r], s);
  }
  visitDictionary(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = T(t.nullBitmap), r = R(e.indices.ArrayType, t.data), { ["dictionary"]: o = new D([new We().visit({ type: e.dictionary })]) } = t, { ["length"]: a = r.length, ["nullCount"]: c = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, a, c, [void 0, r, s], [], o);
  }
  visitInterval(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = T(t.nullBitmap), r = R(e.ArrayType, t.data), { ["length"]: o = r.length / Dt(e), ["nullCount"]: a = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, o, a, [void 0, r, s]);
  }
  visitDuration(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = T(t.nullBitmap), r = R(e.ArrayType, t.data), { ["length"]: o = r.length, ["nullCount"]: a = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, o, a, [void 0, r, s]);
  }
  visitFixedSizeList(t) {
    const { ["type"]: e, ["offset"]: i = 0, ["child"]: s = new We().visit({ type: e.valueType }) } = t, r = T(t.nullBitmap), { ["length"]: o = s.length / Dt(e), ["nullCount"]: a = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, o, a, [void 0, void 0, r], [s]);
  }
  visitMap(t) {
    const { ["type"]: e, ["offset"]: i = 0, ["child"]: s = new We().visit({ type: e.childType }) } = t, r = T(t.nullBitmap), o = Ve(t.valueOffsets), { ["length"]: a = o.length - 1, ["nullCount"]: c = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, a, c, [o, void 0, r], [s]);
  }
}
const Cc = new We();
function I(n) {
  return Cc.visit(n);
}
class Fs {
  constructor(t = 0, e) {
    this.numChunks = t, this.getChunkIterator = e, this.chunkIndex = 0, this.chunkIterator = this.getChunkIterator(0);
  }
  next() {
    for (; this.chunkIndex < this.numChunks; ) {
      const t = this.chunkIterator.next();
      if (!t.done)
        return t;
      ++this.chunkIndex < this.numChunks && (this.chunkIterator = this.getChunkIterator(this.chunkIndex));
    }
    return { done: !0, value: null };
  }
  [Symbol.iterator]() {
    return this;
  }
}
function Ec(n) {
  return n.some((t) => t.nullable);
}
function _o(n) {
  return n.reduce((t, e) => t + e.nullCount, 0);
}
function vo(n) {
  return n.reduce((t, e, i) => (t[i + 1] = t[i] + e.length, t), new Uint32Array(n.length + 1));
}
function wo(n, t, e, i) {
  const s = [];
  for (let r = -1, o = n.length; ++r < o; ) {
    const a = n[r], c = t[r], { length: u } = a;
    if (c >= i)
      break;
    if (e >= c + u)
      continue;
    if (c >= e && c + u <= i) {
      s.push(a);
      continue;
    }
    const d = Math.max(0, e - c), h = Math.min(i - c, u);
    s.push(a.slice(d, h - d));
  }
  return s.length === 0 && s.push(n[0].slice(0, 0)), s;
}
function Gi(n, t, e, i) {
  let s = 0, r = 0, o = t.length - 1;
  do {
    if (s >= o - 1)
      return e < t[o] ? i(n, s, e - t[s]) : null;
    r = s + Math.trunc((o - s) * 0.5), e < t[r] ? o = r : s = r;
  } while (s < o);
}
function Zi(n, t) {
  return n.getValid(t);
}
function En(n) {
  function t(e, i, s) {
    return n(e[i], s);
  }
  return function(e) {
    const i = this.data;
    return Gi(i, this._offsets, e, t);
  };
}
function Io(n) {
  let t;
  function e(i, s, r) {
    return n(i[s], r, t);
  }
  return function(i, s) {
    const r = this.data;
    t = s;
    const o = Gi(r, this._offsets, i, e);
    return t = void 0, o;
  };
}
function So(n) {
  let t;
  function e(i, s, r) {
    let o = r, a = 0, c = 0;
    for (let u = s - 1, d = i.length; ++u < d; ) {
      const h = i[u];
      if (~(a = n(h, t, o)))
        return c + a;
      o = 0, c += h.length;
    }
    return -1;
  }
  return function(i, s) {
    t = i;
    const r = this.data, o = typeof s != "number" ? e(r, 0, 0) : Gi(r, this._offsets, s, e);
    return t = void 0, o;
  };
}
class y extends M {
}
function Vc(n, t) {
  return t === null && n.length > 0 ? 0 : -1;
}
function Rc(n, t) {
  const { nullBitmap: e } = n;
  if (!e || n.nullCount <= 0)
    return -1;
  let i = 0;
  for (const s of new Ki(e, n.offset + (t || 0), n.length, e, bo)) {
    if (!s)
      return i;
    ++i;
  }
  return -1;
}
function A(n, t, e) {
  if (t === void 0)
    return -1;
  if (t === null)
    switch (n.typeId) {
      // Unions don't have a nullBitmap of its own, so compare the `searchElement` to `get()`.
      case l.Union:
        break;
      // Dictionaries do have a nullBitmap, but their dictionary could also have null elements.
      case l.Dictionary:
        break;
      // All other types can iterate the null bitmap
      default:
        return Rc(n, e);
    }
  const i = nt.getVisitFn(n), s = Ne(t);
  for (let r = (e || 0) - 1, o = n.length; ++r < o; )
    if (s(i(n, r)))
      return r;
  return -1;
}
function Bo(n, t, e) {
  const i = nt.getVisitFn(n), s = Ne(t);
  for (let r = (e || 0) - 1, o = n.length; ++r < o; )
    if (s(i(n, r)))
      return r;
  return -1;
}
y.prototype.visitNull = Vc;
y.prototype.visitBool = A;
y.prototype.visitInt = A;
y.prototype.visitInt8 = A;
y.prototype.visitInt16 = A;
y.prototype.visitInt32 = A;
y.prototype.visitInt64 = A;
y.prototype.visitUint8 = A;
y.prototype.visitUint16 = A;
y.prototype.visitUint32 = A;
y.prototype.visitUint64 = A;
y.prototype.visitFloat = A;
y.prototype.visitFloat16 = A;
y.prototype.visitFloat32 = A;
y.prototype.visitFloat64 = A;
y.prototype.visitUtf8 = A;
y.prototype.visitLargeUtf8 = A;
y.prototype.visitBinary = A;
y.prototype.visitLargeBinary = A;
y.prototype.visitFixedSizeBinary = A;
y.prototype.visitDate = A;
y.prototype.visitDateDay = A;
y.prototype.visitDateMillisecond = A;
y.prototype.visitTimestamp = A;
y.prototype.visitTimestampSecond = A;
y.prototype.visitTimestampMillisecond = A;
y.prototype.visitTimestampMicrosecond = A;
y.prototype.visitTimestampNanosecond = A;
y.prototype.visitTime = A;
y.prototype.visitTimeSecond = A;
y.prototype.visitTimeMillisecond = A;
y.prototype.visitTimeMicrosecond = A;
y.prototype.visitTimeNanosecond = A;
y.prototype.visitDecimal = A;
y.prototype.visitList = A;
y.prototype.visitStruct = A;
y.prototype.visitUnion = A;
y.prototype.visitDenseUnion = Bo;
y.prototype.visitSparseUnion = Bo;
y.prototype.visitDictionary = A;
y.prototype.visitInterval = A;
y.prototype.visitIntervalDayTime = A;
y.prototype.visitIntervalYearMonth = A;
y.prototype.visitIntervalMonthDayNano = A;
y.prototype.visitDuration = A;
y.prototype.visitDurationSecond = A;
y.prototype.visitDurationMillisecond = A;
y.prototype.visitDurationMicrosecond = A;
y.prototype.visitDurationNanosecond = A;
y.prototype.visitFixedSizeList = A;
y.prototype.visitMap = A;
const Vn = new y();
class g extends M {
}
function w(n) {
  const { type: t } = n;
  if (n.nullCount === 0 && n.stride === 1 && // Don't defer to native iterator for timestamps since Numbers are expected
  // (DataType.isTimestamp(type)) && type.unit === TimeUnit.MILLISECOND ||
  (f.isInt(t) && t.bitWidth !== 64 || f.isTime(t) && t.bitWidth !== 64 || f.isFloat(t) && t.precision !== W.HALF))
    return new Fs(n.data.length, (i) => {
      const s = n.data[i];
      return s.values.subarray(0, s.length)[Symbol.iterator]();
    });
  let e = 0;
  return new Fs(n.data.length, (i) => {
    const r = n.data[i].length, o = n.slice(e, e + r);
    return e += r, new zc(o);
  });
}
class zc {
  constructor(t) {
    this.vector = t, this.index = 0;
  }
  next() {
    return this.index < this.vector.length ? {
      value: this.vector.get(this.index++)
    } : { done: !0, value: null };
  }
  [Symbol.iterator]() {
    return this;
  }
}
g.prototype.visitNull = w;
g.prototype.visitBool = w;
g.prototype.visitInt = w;
g.prototype.visitInt8 = w;
g.prototype.visitInt16 = w;
g.prototype.visitInt32 = w;
g.prototype.visitInt64 = w;
g.prototype.visitUint8 = w;
g.prototype.visitUint16 = w;
g.prototype.visitUint32 = w;
g.prototype.visitUint64 = w;
g.prototype.visitFloat = w;
g.prototype.visitFloat16 = w;
g.prototype.visitFloat32 = w;
g.prototype.visitFloat64 = w;
g.prototype.visitUtf8 = w;
g.prototype.visitLargeUtf8 = w;
g.prototype.visitBinary = w;
g.prototype.visitLargeBinary = w;
g.prototype.visitFixedSizeBinary = w;
g.prototype.visitDate = w;
g.prototype.visitDateDay = w;
g.prototype.visitDateMillisecond = w;
g.prototype.visitTimestamp = w;
g.prototype.visitTimestampSecond = w;
g.prototype.visitTimestampMillisecond = w;
g.prototype.visitTimestampMicrosecond = w;
g.prototype.visitTimestampNanosecond = w;
g.prototype.visitTime = w;
g.prototype.visitTimeSecond = w;
g.prototype.visitTimeMillisecond = w;
g.prototype.visitTimeMicrosecond = w;
g.prototype.visitTimeNanosecond = w;
g.prototype.visitDecimal = w;
g.prototype.visitList = w;
g.prototype.visitStruct = w;
g.prototype.visitUnion = w;
g.prototype.visitDenseUnion = w;
g.prototype.visitSparseUnion = w;
g.prototype.visitDictionary = w;
g.prototype.visitInterval = w;
g.prototype.visitIntervalDayTime = w;
g.prototype.visitIntervalYearMonth = w;
g.prototype.visitIntervalMonthDayNano = w;
g.prototype.visitDuration = w;
g.prototype.visitDurationSecond = w;
g.prototype.visitDurationMillisecond = w;
g.prototype.visitDurationMicrosecond = w;
g.prototype.visitDurationNanosecond = w;
g.prototype.visitFixedSizeList = w;
g.prototype.visitMap = w;
const Qi = new g();
var Ao;
const Do = {}, Oo = {};
class D {
  constructor(t) {
    var e, i, s;
    const r = t[0] instanceof D ? t.flatMap((a) => a.data) : t;
    if (r.length === 0 || r.some((a) => !(a instanceof L)))
      throw new TypeError("Vector constructor expects an Array of Data instances.");
    const o = (e = r[0]) === null || e === void 0 ? void 0 : e.type;
    switch (r.length) {
      case 0:
        this._offsets = [0];
        break;
      case 1: {
        const { get: a, set: c, indexOf: u } = Do[o.typeId], d = r[0];
        this.isValid = (h) => Zi(d, h), this.get = (h) => a(d, h), this.set = (h, N) => c(d, h, N), this.indexOf = (h) => u(d, h), this._offsets = [0, d.length];
        break;
      }
      default:
        Object.setPrototypeOf(this, Oo[o.typeId]), this._offsets = vo(r);
        break;
    }
    this.data = r, this.type = o, this.stride = Dt(o), this.numChildren = (s = (i = o.children) === null || i === void 0 ? void 0 : i.length) !== null && s !== void 0 ? s : 0, this.length = this._offsets.at(-1);
  }
  /**
   * The aggregate size (in bytes) of this Vector's buffers and/or child Vectors.
   */
  get byteLength() {
    return this.data.reduce((t, e) => t + e.byteLength, 0);
  }
  /**
   * Whether this Vector's elements can contain null values.
   */
  get nullable() {
    return Ec(this.data);
  }
  /**
   * The number of null elements in this Vector.
   */
  get nullCount() {
    return _o(this.data);
  }
  /**
   * The Array or TypedArray constructor used for the JS representation
   *  of the element's values in {@link Vector.prototype.toArray `toArray()`}.
   */
  get ArrayType() {
    return this.type.ArrayType;
  }
  /**
   * The name that should be printed when the Vector is logged in a message.
   */
  get [Symbol.toStringTag]() {
    return `${this.VectorName}<${this.type[Symbol.toStringTag]}>`;
  }
  /**
   * The name of this Vector.
   */
  get VectorName() {
    return `${l[this.type.typeId]}Vector`;
  }
  /**
   * Check whether an element is null.
   * @param index The index at which to read the validity bitmap.
   */
  // @ts-ignore
  isValid(t) {
    return !1;
  }
  /**
   * Get an element value by position.
   * @param index The index of the element to read.
   */
  // @ts-ignore
  get(t) {
    return null;
  }
  /**
   * Get an element value by position.
   * @param index The index of the element to read. A negative index will count back from the last element.
   */
  at(t) {
    return this.get(Ji(t, this.length));
  }
  /**
   * Set an element value by position.
   * @param index The index of the element to write.
   * @param value The value to set.
   */
  // @ts-ignore
  set(t, e) {
  }
  /**
   * Retrieve the index of the first occurrence of a value in an Vector.
   * @param element The value to locate in the Vector.
   * @param offset The index at which to begin the search. If offset is omitted, the search starts at index 0.
   */
  // @ts-ignore
  indexOf(t, e) {
    return -1;
  }
  includes(t, e) {
    return this.indexOf(t, e) > -1;
  }
  /**
   * Iterator for the Vector's elements.
   */
  [Symbol.iterator]() {
    return Qi.visit(this);
  }
  /**
   * Combines two or more Vectors of the same type.
   * @param others Additional Vectors to add to the end of this Vector.
   */
  concat(...t) {
    return new D(this.data.concat(t.flatMap((e) => e.data).flat(Number.POSITIVE_INFINITY)));
  }
  /**
   * Return a zero-copy sub-section of this Vector.
   * @param start The beginning of the specified portion of the Vector.
   * @param end The end of the specified portion of the Vector. This is exclusive of the element at the index 'end'.
   */
  slice(t, e) {
    return new D(mo(this, t, e, ({ data: i, _offsets: s }, r, o) => wo(i, s, r, o)));
  }
  toJSON() {
    return [...this];
  }
  /**
   * Return a JavaScript Array or TypedArray of the Vector's elements.
   *
   * @note If this Vector contains a single Data chunk and the Vector's type is a
   *  primitive numeric type corresponding to one of the JavaScript TypedArrays, this
   *  method returns a zero-copy slice of the underlying TypedArray values. If there's
   *  more than one chunk, the resulting TypedArray will be a copy of the data from each
   *  chunk's underlying TypedArray values.
   *
   * @returns An Array or TypedArray of the Vector's elements, based on the Vector's DataType.
   */
  toArray() {
    const { type: t, data: e, length: i, stride: s, ArrayType: r } = this;
    switch (t.typeId) {
      case l.Int:
      case l.Float:
      case l.Decimal:
      case l.Time:
      case l.Timestamp:
        switch (e.length) {
          case 0:
            return new r();
          case 1:
            return e[0].values.subarray(0, i * s);
          default:
            return e.reduce((o, { values: a, length: c }) => (o.array.set(a.subarray(0, c * s), o.offset), o.offset += c * s, o), { array: new r(i * s), offset: 0 }).array;
        }
    }
    return [...this];
  }
  /**
   * Returns a string representation of the Vector.
   *
   * @returns A string representation of the Vector.
   */
  toString() {
    return `[${[...this].join(",")}]`;
  }
  /**
   * Returns a child Vector by name, or null if this Vector has no child with the given name.
   * @param name The name of the child to retrieve.
   */
  getChild(t) {
    var e;
    return this.getChildAt((e = this.type.children) === null || e === void 0 ? void 0 : e.findIndex((i) => i.name === t));
  }
  /**
   * Returns a child Vector by index, or null if this Vector has no child at the supplied index.
   * @param index The index of the child to retrieve.
   */
  getChildAt(t) {
    return t > -1 && t < this.numChildren ? new D(this.data.map(({ children: e }) => e[t])) : null;
  }
  get isMemoized() {
    return f.isDictionary(this.type) ? this.data[0].dictionary.isMemoized : !1;
  }
  /**
   * Adds memoization to the Vector's {@link get} method. For dictionary
   * vectors, this method return a vector that memoizes only the dictionary
   * values.
   *
   * Memoization is very useful when decoding a value is expensive such as
   * Utf8. The memoization creates a cache of the size of the Vector and
   * therefore increases memory usage.
   *
   * @returns A new vector that memoizes calls to {@link get}.
   */
  memoize() {
    if (f.isDictionary(this.type)) {
      const t = new Rn(this.data[0].dictionary), e = this.data.map((i) => {
        const s = i.clone();
        return s.dictionary = t, s;
      });
      return new D(e);
    }
    return new Rn(this);
  }
  /**
   * Returns a vector without memoization of the {@link get} method. If this
   * vector is not memoized, this method returns this vector.
   *
   * @returns A new vector without memoization.
   */
  unmemoize() {
    if (f.isDictionary(this.type) && this.isMemoized) {
      const t = this.data[0].dictionary.unmemoize(), e = this.data.map((i) => {
        const s = i.clone();
        return s.dictionary = t, s;
      });
      return new D(e);
    }
    return this;
  }
}
Ao = Symbol.toStringTag;
D[Ao] = ((n) => {
  n.type = f.prototype, n.data = [], n.length = 0, n.stride = 1, n.numChildren = 0, n._offsets = new Uint32Array([0]), n[Symbol.isConcatSpreadable] = !0;
  const t = Object.keys(l).map((e) => l[e]).filter((e) => typeof e == "number" && e !== l.NONE);
  for (const e of t) {
    const i = nt.getVisitFnByTypeId(e), s = pt.getVisitFnByTypeId(e), r = Vn.getVisitFnByTypeId(e);
    Do[e] = { get: i, set: s, indexOf: r }, Oo[e] = Object.create(n, {
      isValid: { value: En(Zi) },
      get: { value: En(nt.getVisitFnByTypeId(e)) },
      set: { value: Io(pt.getVisitFnByTypeId(e)) },
      indexOf: { value: So(Vn.getVisitFnByTypeId(e)) }
    });
  }
  return "Vector";
})(D.prototype);
class Rn extends D {
  constructor(t) {
    super(t.data);
    const e = this.get, i = this.set, s = this.slice, r = new Array(this.length);
    Object.defineProperty(this, "get", {
      value(o) {
        const a = r[o];
        if (a !== void 0)
          return a;
        const c = e.call(this, o);
        return r[o] = c, c;
      }
    }), Object.defineProperty(this, "set", {
      value(o, a) {
        i.call(this, o, a), r[o] = a;
      }
    }), Object.defineProperty(this, "slice", {
      value: (o, a) => new Rn(s.call(this, o, a))
    }), Object.defineProperty(this, "isMemoized", { value: !0 }), Object.defineProperty(this, "unmemoize", {
      value: () => new D(this.data)
    }), Object.defineProperty(this, "memoize", {
      value: () => this
    });
  }
}
function Fo(n) {
  if (n) {
    if (n instanceof L)
      return new D([n]);
    if (n instanceof D)
      return new D(n.data);
    if (n.type instanceof f)
      return new D([I(n)]);
    if (Array.isArray(n))
      return new D(n.flatMap((t) => kc(t)));
    if (ArrayBuffer.isView(n)) {
      n instanceof DataView && (n = new Uint8Array(n.buffer));
      const t = { offset: 0, length: n.length, nullCount: -1, data: n };
      if (n instanceof Int8Array)
        return new D([I(Object.assign(Object.assign({}, t), { type: new Br() }))]);
      if (n instanceof Int16Array)
        return new D([I(Object.assign(Object.assign({}, t), { type: new Ar() }))]);
      if (n instanceof Int32Array)
        return new D([I(Object.assign(Object.assign({}, t), { type: new Jt() }))]);
      if (n instanceof BigInt64Array)
        return new D([I(Object.assign(Object.assign({}, t), { type: new Fi() }))]);
      if (n instanceof Uint8Array || n instanceof Uint8ClampedArray)
        return new D([I(Object.assign(Object.assign({}, t), { type: new Dr() }))]);
      if (n instanceof Uint16Array)
        return new D([I(Object.assign(Object.assign({}, t), { type: new Or() }))]);
      if (n instanceof Uint32Array)
        return new D([I(Object.assign(Object.assign({}, t), { type: new Fr() }))]);
      if (n instanceof BigUint64Array)
        return new D([I(Object.assign(Object.assign({}, t), { type: new Mr() }))]);
      if (n instanceof Float32Array)
        return new D([I(Object.assign(Object.assign({}, t), { type: new Nr() }))]);
      if (n instanceof Float64Array)
        return new D([I(Object.assign(Object.assign({}, t), { type: new Jn() }))]);
      throw new Error("Unrecognized input");
    }
  }
  throw new Error("Unrecognized input");
}
function kc(n) {
  return n instanceof L ? [n] : n instanceof D ? n.data : Fo(n).data;
}
function Pc(n) {
  if (!n || n.length <= 0)
    return function(s) {
      return !0;
    };
  let t = "";
  const e = n.filter((i) => i === i);
  return e.length > 0 && (t = `
    switch (x) {${e.map((i) => `
        case ${jc(i)}:`).join("")}
            return false;
    }`), n.length !== e.length && (t = `if (x !== x) return false;
${t}`), new Function("x", `${t}
return true;`);
}
function jc(n) {
  return typeof n != "bigint" ? ie(n) : `${ie(n)}n`;
}
function oi(n, t) {
  const e = Math.ceil(n) * t - 1;
  return (e - e % 64 + 64 || 64) / t;
}
function Ms(n, t = 0) {
  return n.length >= t ? n.subarray(0, t) : hi(new n.constructor(t), n, 0);
}
class rn {
  constructor(t, e = 0, i = 1) {
    this.length = Math.ceil(e / i), this.buffer = new t(this.length), this.stride = i, this.BYTES_PER_ELEMENT = t.BYTES_PER_ELEMENT, this.ArrayType = t;
  }
  get byteLength() {
    return Math.ceil(this.length * this.stride) * this.BYTES_PER_ELEMENT;
  }
  get reservedLength() {
    return this.buffer.length / this.stride;
  }
  get reservedByteLength() {
    return this.buffer.byteLength;
  }
  // @ts-ignore
  set(t, e) {
    return this;
  }
  append(t) {
    return this.set(this.length, t);
  }
  reserve(t) {
    if (t > 0) {
      this.length += t;
      const e = this.stride, i = this.length * e, s = this.buffer.length;
      i >= s && this._resize(s === 0 ? oi(i * 1, this.BYTES_PER_ELEMENT) : oi(i * 2, this.BYTES_PER_ELEMENT));
    }
    return this;
  }
  flush(t = this.length) {
    t = oi(t * this.stride, this.BYTES_PER_ELEMENT);
    const e = Ms(this.buffer, t);
    return this.clear(), e;
  }
  clear() {
    return this.length = 0, this.buffer = new this.ArrayType(), this;
  }
  _resize(t) {
    return this.buffer = Ms(this.buffer, t);
  }
}
class on extends rn {
  last() {
    return this.get(this.length - 1);
  }
  get(t) {
    return this.buffer[t];
  }
  set(t, e) {
    return this.reserve(t - this.length + 1), this.buffer[t * this.stride] = e, this;
  }
}
class Mo extends on {
  constructor() {
    super(Uint8Array, 0, 1 / 8), this.numValid = 0;
  }
  get numInvalid() {
    return this.length - this.numValid;
  }
  get(t) {
    return this.buffer[t >> 3] >> t % 8 & 1;
  }
  set(t, e) {
    const { buffer: i } = this.reserve(t - this.length + 1), s = t >> 3, r = t % 8, o = i[s] >> r & 1;
    return e ? o === 0 && (i[s] |= 1 << r, ++this.numValid) : o === 1 && (i[s] &= ~(1 << r), --this.numValid), this;
  }
  clear() {
    return this.numValid = 0, super.clear();
  }
}
class No extends on {
  constructor(t) {
    super(t.OffsetArrayType, 1, 1);
  }
  append(t) {
    return this.set(this.length - 1, t);
  }
  set(t, e) {
    const i = this.length - 1, s = this.reserve(t - i + 1).buffer;
    return i < t++ && i >= 0 && s.fill(s[i], i, t), s[t] = s[t - 1] + e, this;
  }
  flush(t = this.length - 1) {
    return t > this.length && this.set(t - 1, this.BYTES_PER_ELEMENT > 4 ? BigInt(0) : 0), super.flush(t + 1);
  }
}
let it = class {
  /** @nocollapse */
  // @ts-ignore
  static throughNode(t) {
    throw new Error('"throughNode" not available in this environment');
  }
  /** @nocollapse */
  // @ts-ignore
  static throughDOM(t) {
    throw new Error('"throughDOM" not available in this environment');
  }
  /**
   * Construct a builder with the given Arrow DataType with optional null values,
   * which will be interpreted as "null" when set or appended to the `Builder`.
   * @param {{ type: T, nullValues?: any[] }} options A `BuilderOptions` object used to create this `Builder`.
   */
  constructor({ type: t, nullValues: e }) {
    this.length = 0, this.finished = !1, this.type = t, this.children = [], this.nullValues = e, this.stride = Dt(t), this._nulls = new Mo(), e && e.length > 0 && (this._isValid = Pc(e));
  }
  /**
   * Flush the `Builder` and return a `Vector<T>`.
   * @returns {Vector<T>} A `Vector<T>` of the flushed values.
   */
  toVector() {
    return new D([this.flush()]);
  }
  get ArrayType() {
    return this.type.ArrayType;
  }
  get nullCount() {
    return this._nulls.numInvalid;
  }
  get numChildren() {
    return this.children.length;
  }
  /**
   * @returns The aggregate length (in bytes) of the values that have been written.
   */
  get byteLength() {
    let t = 0;
    const { _offsets: e, _values: i, _nulls: s, _typeIds: r, children: o } = this;
    return e && (t += e.byteLength), i && (t += i.byteLength), s && (t += s.byteLength), r && (t += r.byteLength), o.reduce((a, c) => a + c.byteLength, t);
  }
  /**
   * @returns The aggregate number of rows that have been reserved to write new values.
   */
  get reservedLength() {
    return this._nulls.reservedLength;
  }
  /**
   * @returns The aggregate length (in bytes) that has been reserved to write new values.
   */
  get reservedByteLength() {
    let t = 0;
    return this._offsets && (t += this._offsets.reservedByteLength), this._values && (t += this._values.reservedByteLength), this._nulls && (t += this._nulls.reservedByteLength), this._typeIds && (t += this._typeIds.reservedByteLength), this.children.reduce((e, i) => e + i.reservedByteLength, t);
  }
  get valueOffsets() {
    return this._offsets ? this._offsets.buffer : null;
  }
  get values() {
    return this._values ? this._values.buffer : null;
  }
  get nullBitmap() {
    return this._nulls ? this._nulls.buffer : null;
  }
  get typeIds() {
    return this._typeIds ? this._typeIds.buffer : null;
  }
  /**
   * Appends a value (or null) to this `Builder`.
   * This is equivalent to `builder.set(builder.length, value)`.
   * @param {T['TValue'] | TNull } value The value to append.
   */
  append(t) {
    return this.set(this.length, t);
  }
  /**
   * Validates whether a value is valid (true), or null (false)
   * @param {T['TValue'] | TNull } value The value to compare against null the value representations
   */
  isValid(t) {
    return this._isValid(t);
  }
  /**
   * Write a value (or null-value sentinel) at the supplied index.
   * If the value matches one of the null-value representations, a 1-bit is
   * written to the null `BitmapBufferBuilder`. Otherwise, a 0 is written to
   * the null `BitmapBufferBuilder`, and the value is passed to
   * `Builder.prototype.setValue()`.
   * @param {number} index The index of the value to write.
   * @param {T['TValue'] | TNull } value The value to write at the supplied index.
   * @returns {this} The updated `Builder` instance.
   */
  set(t, e) {
    return this.setValid(t, this.isValid(e)) && this.setValue(t, e), this;
  }
  /**
   * Write a value to the underlying buffers at the supplied index, bypassing
   * the null-value check. This is a low-level method that
   * @param {number} index
   * @param {T['TValue'] | TNull } value
   */
  setValue(t, e) {
    this._setValue(this, t, e);
  }
  setValid(t, e) {
    return this.length = this._nulls.set(t, +e).length, e;
  }
  // @ts-ignore
  addChild(t, e = `${this.numChildren}`) {
    throw new Error(`Cannot append children to non-nested type "${this.type}"`);
  }
  /**
   * Retrieve the child `Builder` at the supplied `index`, or null if no child
   * exists at that index.
   * @param {number} index The index of the child `Builder` to retrieve.
   * @returns {Builder | null} The child Builder at the supplied index or null.
   */
  getChildAt(t) {
    return this.children[t] || null;
  }
  /**
   * Commit all the values that have been written to their underlying
   * ArrayBuffers, including any child Builders if applicable, and reset
   * the internal `Builder` state.
   * @returns A `Data<T>` of the buffers and children representing the values written.
   */
  flush() {
    let t, e, i, s;
    const { type: r, length: o, nullCount: a, _typeIds: c, _offsets: u, _values: d, _nulls: h } = this;
    (e = c?.flush(o)) ? s = u?.flush(o) : (s = u?.flush(o)) ? t = d?.flush(u.last()) : t = d?.flush(o), a > 0 && (i = h?.flush(o));
    const N = this.children.map((B) => B.flush());
    return this.clear(), I({
      type: r,
      length: o,
      nullCount: a,
      children: N,
      child: N[0],
      data: t,
      typeIds: e,
      nullBitmap: i,
      valueOffsets: s
    });
  }
  /**
   * Finalize this `Builder`, and child builders if applicable.
   * @returns {this} The finalized `Builder` instance.
   */
  finish() {
    this.finished = !0;
    for (const t of this.children)
      t.finish();
    return this;
  }
  /**
   * Clear this Builder's internal state, including child Builders if applicable, and reset the length to 0.
   * @returns {this} The cleared `Builder` instance.
   */
  clear() {
    var t, e, i, s;
    this.length = 0, (t = this._nulls) === null || t === void 0 || t.clear(), (e = this._values) === null || e === void 0 || e.clear(), (i = this._offsets) === null || i === void 0 || i.clear(), (s = this._typeIds) === null || s === void 0 || s.clear();
    for (const r of this.children)
      r.clear();
    return this;
  }
};
it.prototype.length = 1;
it.prototype.stride = 1;
it.prototype.children = null;
it.prototype.finished = !1;
it.prototype.nullValues = null;
it.prototype._isValid = () => !0;
class Pt extends it {
  constructor(t) {
    super(t), this._values = new on(this.ArrayType, 0, this.stride);
  }
  setValue(t, e) {
    const i = this._values;
    return i.reserve(t - i.length + 1), super.setValue(t, e);
  }
}
class Te extends it {
  constructor(t) {
    super(t), this._pendingLength = 0, this._offsets = new No(t.type);
  }
  setValue(t, e) {
    const i = this._pending || (this._pending = /* @__PURE__ */ new Map()), s = i.get(t);
    s && (this._pendingLength -= s.length), this._pendingLength += e instanceof Kn ? e[Xt].length : e.length, i.set(t, e);
  }
  setValid(t, e) {
    return super.setValid(t, e) ? !0 : ((this._pending || (this._pending = /* @__PURE__ */ new Map())).set(t, void 0), !1);
  }
  clear() {
    return this._pendingLength = 0, this._pending = void 0, super.clear();
  }
  flush() {
    return this._flush(), super.flush();
  }
  finish() {
    return this._flush(), super.finish();
  }
  _flush() {
    const t = this._pending, e = this._pendingLength;
    return this._pendingLength = 0, this._pending = void 0, t && t.size > 0 && this._flushPending(t, e), this;
  }
}
class vi {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  /**
   * Index to the start of the RecordBlock (note this is past the Message header)
   */
  offset() {
    return this.bb.readInt64(this.bb_pos);
  }
  /**
   * Length of the metadata
   */
  metaDataLength() {
    return this.bb.readInt32(this.bb_pos + 8);
  }
  /**
   * Length of the data (this is aligned so there can be a gap between this and
   * the metadata).
   */
  bodyLength() {
    return this.bb.readInt64(this.bb_pos + 16);
  }
  static sizeOf() {
    return 24;
  }
  static createBlock(t, e, i, s) {
    return t.prep(8, 24), t.writeInt64(BigInt(s ?? 0)), t.pad(4), t.writeInt32(i), t.writeInt64(BigInt(e ?? 0)), t.offset();
  }
}
class st {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsFooter(t, e) {
    return (e || new st()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsFooter(t, e) {
    return t.setPosition(t.position() + E), (e || new st()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  version() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt16(this.bb_pos + t) : $.V1;
  }
  schema(t) {
    const e = this.bb.__offset(this.bb_pos, 6);
    return e ? (t || new Bt()).__init(this.bb.__indirect(this.bb_pos + e), this.bb) : null;
  }
  dictionaries(t, e) {
    const i = this.bb.__offset(this.bb_pos, 8);
    return i ? (e || new vi()).__init(this.bb.__vector(this.bb_pos + i) + t * 24, this.bb) : null;
  }
  dictionariesLength() {
    const t = this.bb.__offset(this.bb_pos, 8);
    return t ? this.bb.__vector_len(this.bb_pos + t) : 0;
  }
  recordBatches(t, e) {
    const i = this.bb.__offset(this.bb_pos, 10);
    return i ? (e || new vi()).__init(this.bb.__vector(this.bb_pos + i) + t * 24, this.bb) : null;
  }
  recordBatchesLength() {
    const t = this.bb.__offset(this.bb_pos, 10);
    return t ? this.bb.__vector_len(this.bb_pos + t) : 0;
  }
  /**
   * User-defined metadata
   */
  customMetadata(t, e) {
    const i = this.bb.__offset(this.bb_pos, 12);
    return i ? (e || new H()).__init(this.bb.__indirect(this.bb.__vector(this.bb_pos + i) + t * 4), this.bb) : null;
  }
  customMetadataLength() {
    const t = this.bb.__offset(this.bb_pos, 12);
    return t ? this.bb.__vector_len(this.bb_pos + t) : 0;
  }
  static startFooter(t) {
    t.startObject(5);
  }
  static addVersion(t, e) {
    t.addFieldInt16(0, e, $.V1);
  }
  static addSchema(t, e) {
    t.addFieldOffset(1, e, 0);
  }
  static addDictionaries(t, e) {
    t.addFieldOffset(2, e, 0);
  }
  static startDictionariesVector(t, e) {
    t.startVector(24, e, 8);
  }
  static addRecordBatches(t, e) {
    t.addFieldOffset(3, e, 0);
  }
  static startRecordBatchesVector(t, e) {
    t.startVector(24, e, 8);
  }
  static addCustomMetadata(t, e) {
    t.addFieldOffset(4, e, 0);
  }
  static createCustomMetadataVector(t, e) {
    t.startVector(4, e.length, 4);
    for (let i = e.length - 1; i >= 0; i--)
      t.addOffset(e[i]);
    return t.endVector();
  }
  static startCustomMetadataVector(t, e) {
    t.startVector(4, e, 4);
  }
  static endFooter(t) {
    return t.endObject();
  }
  static finishFooterBuffer(t, e) {
    t.finish(e);
  }
  static finishSizePrefixedFooterBuffer(t, e) {
    t.finish(e, void 0, !0);
  }
}
class C {
  constructor(t = [], e, i, s = $.V5) {
    this.fields = t || [], this.metadata = e || /* @__PURE__ */ new Map(), i || (i = wi(this.fields)), this.dictionaries = i, this.metadataVersion = s;
  }
  get [Symbol.toStringTag]() {
    return "Schema";
  }
  get names() {
    return this.fields.map((t) => t.name);
  }
  toString() {
    return `Schema<{ ${this.fields.map((t, e) => `${e}: ${t}`).join(", ")} }>`;
  }
  /**
   * Construct a new Schema containing only specified fields.
   *
   * @param fieldNames Names of fields to keep.
   * @returns A new Schema of fields matching the specified names.
   */
  select(t) {
    const e = new Set(t), i = this.fields.filter((s) => e.has(s.name));
    return new C(i, this.metadata);
  }
  /**
   * Construct a new Schema containing only fields at the specified indices.
   *
   * @param fieldIndices Indices of fields to keep.
   * @returns A new Schema of fields at the specified indices.
   */
  selectAt(t) {
    const e = t.map((i) => this.fields[i]).filter(Boolean);
    return new C(e, this.metadata);
  }
  assign(...t) {
    const e = t[0] instanceof C ? t[0] : Array.isArray(t[0]) ? new C(t[0]) : new C(t), i = [...this.fields], s = yn(yn(/* @__PURE__ */ new Map(), this.metadata), e.metadata), r = e.fields.filter((a) => {
      const c = i.findIndex((u) => u.name === a.name);
      return ~c ? (i[c] = a.clone({
        metadata: yn(yn(/* @__PURE__ */ new Map(), i[c].metadata), a.metadata)
      })) && !1 : !0;
    }), o = wi(r, /* @__PURE__ */ new Map());
    return new C([...i, ...r], s, new Map([...this.dictionaries, ...o]));
  }
}
C.prototype.fields = null;
C.prototype.metadata = null;
C.prototype.dictionaries = null;
class x {
  /** @nocollapse */
  static new(...t) {
    let [e, i, s, r] = t;
    return t[0] && typeof t[0] == "object" && ({ name: e } = t[0], i === void 0 && (i = t[0].type), s === void 0 && (s = t[0].nullable), r === void 0 && (r = t[0].metadata)), new x(`${e}`, i, s, r);
  }
  constructor(t, e, i = !1, s) {
    this.name = t, this.type = e, this.nullable = i, this.metadata = s || /* @__PURE__ */ new Map();
  }
  get typeId() {
    return this.type.typeId;
  }
  get [Symbol.toStringTag]() {
    return "Field";
  }
  toString() {
    return `${this.name}: ${this.type}`;
  }
  clone(...t) {
    let [e, i, s, r] = t;
    return !t[0] || typeof t[0] != "object" ? [e = this.name, i = this.type, s = this.nullable, r = this.metadata] = t : { name: e = this.name, type: i = this.type, nullable: s = this.nullable, metadata: r = this.metadata } = t[0], x.new(e, i, s, r);
  }
}
x.prototype.type = null;
x.prototype.name = null;
x.prototype.nullable = null;
x.prototype.metadata = null;
function yn(n, t) {
  return new Map([...n || /* @__PURE__ */ new Map(), ...t || /* @__PURE__ */ new Map()]);
}
function wi(n, t = /* @__PURE__ */ new Map()) {
  for (let e = -1, i = n.length; ++e < i; ) {
    const r = n[e].type;
    if (f.isDictionary(r)) {
      if (!t.has(r.id))
        t.set(r.id, r.dictionary);
      else if (t.get(r.id) !== r.dictionary)
        throw new Error("Cannot create Schema containing two different dictionaries with the same Id");
    }
    r.children && r.children.length > 0 && wi(r.children, t);
  }
  return t;
}
var $c = Gs, Yc = ee;
class Xi {
  /** @nocollapse */
  static decode(t) {
    t = new Yc(T(t));
    const e = st.getRootAsFooter(t), i = C.decode(e.schema(), /* @__PURE__ */ new Map(), e.version());
    return new Hc(i, e);
  }
  /** @nocollapse */
  static encode(t) {
    const e = new $c(), i = C.encode(e, t.schema);
    st.startRecordBatchesVector(e, t.numRecordBatches);
    for (const o of [...t.recordBatches()].slice().reverse())
      Oe.encode(e, o);
    const s = e.endVector();
    st.startDictionariesVector(e, t.numDictionaries);
    for (const o of [...t.dictionaryBatches()].slice().reverse())
      Oe.encode(e, o);
    const r = e.endVector();
    return st.startFooter(e), st.addSchema(e, i), st.addVersion(e, $.V5), st.addRecordBatches(e, s), st.addDictionaries(e, r), st.finishFooterBuffer(e, st.endFooter(e)), e.asUint8Array();
  }
  get numRecordBatches() {
    return this._recordBatches.length;
  }
  get numDictionaries() {
    return this._dictionaryBatches.length;
  }
  constructor(t, e = $.V5, i, s) {
    this.schema = t, this.version = e, i && (this._recordBatches = i), s && (this._dictionaryBatches = s);
  }
  *recordBatches() {
    for (let t, e = -1, i = this.numRecordBatches; ++e < i; )
      (t = this.getRecordBatch(e)) && (yield t);
  }
  *dictionaryBatches() {
    for (let t, e = -1, i = this.numDictionaries; ++e < i; )
      (t = this.getDictionaryBatch(e)) && (yield t);
  }
  getRecordBatch(t) {
    return t >= 0 && t < this.numRecordBatches && this._recordBatches[t] || null;
  }
  getDictionaryBatch(t) {
    return t >= 0 && t < this.numDictionaries && this._dictionaryBatches[t] || null;
  }
}
class Hc extends Xi {
  get numRecordBatches() {
    return this._footer.recordBatchesLength();
  }
  get numDictionaries() {
    return this._footer.dictionariesLength();
  }
  constructor(t, e) {
    super(t, e.version()), this._footer = e;
  }
  getRecordBatch(t) {
    if (t >= 0 && t < this.numRecordBatches) {
      const e = this._footer.recordBatches(t);
      if (e)
        return Oe.decode(e);
    }
    return null;
  }
  getDictionaryBatch(t) {
    if (t >= 0 && t < this.numDictionaries) {
      const e = this._footer.dictionaries(t);
      if (e)
        return Oe.decode(e);
    }
    return null;
  }
}
class Oe {
  /** @nocollapse */
  static decode(t) {
    return new Oe(t.metaDataLength(), t.bodyLength(), t.offset());
  }
  /** @nocollapse */
  static encode(t, e) {
    const { metaDataLength: i } = e, s = BigInt(e.offset), r = BigInt(e.bodyLength);
    return vi.createBlock(t, s, i, r);
  }
  constructor(t, e, i) {
    this.metaDataLength = t, this.offset = P(i), this.bodyLength = P(e);
  }
}
let Ht = class It {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsMessage(t, e) {
    return (e || new It()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsMessage(t, e) {
    return t.setPosition(t.position() + E), (e || new It()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  version() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt16(this.bb_pos + t) : $.V1;
  }
  headerType() {
    const t = this.bb.__offset(this.bb_pos, 6);
    return t ? this.bb.readUint8(this.bb_pos + t) : U.NONE;
  }
  header(t) {
    const e = this.bb.__offset(this.bb_pos, 8);
    return e ? this.bb.__union(t, this.bb_pos + e) : null;
  }
  bodyLength() {
    const t = this.bb.__offset(this.bb_pos, 10);
    return t ? this.bb.readInt64(this.bb_pos + t) : BigInt("0");
  }
  customMetadata(t, e) {
    const i = this.bb.__offset(this.bb_pos, 12);
    return i ? (e || new H()).__init(this.bb.__indirect(this.bb.__vector(this.bb_pos + i) + t * 4), this.bb) : null;
  }
  customMetadataLength() {
    const t = this.bb.__offset(this.bb_pos, 12);
    return t ? this.bb.__vector_len(this.bb_pos + t) : 0;
  }
  static startMessage(t) {
    t.startObject(5);
  }
  static addVersion(t, e) {
    t.addFieldInt16(0, e, $.V1);
  }
  static addHeaderType(t, e) {
    t.addFieldInt8(1, e, U.NONE);
  }
  static addHeader(t, e) {
    t.addFieldOffset(2, e, 0);
  }
  static addBodyLength(t, e) {
    t.addFieldInt64(3, e, BigInt("0"));
  }
  static addCustomMetadata(t, e) {
    t.addFieldOffset(4, e, 0);
  }
  static createCustomMetadataVector(t, e) {
    t.startVector(4, e.length, 4);
    for (let i = e.length - 1; i >= 0; i--)
      t.addOffset(e[i]);
    return t.endVector();
  }
  static startCustomMetadataVector(t, e) {
    t.startVector(4, e, 4);
  }
  static endMessage(t) {
    return t.endObject();
  }
  static finishMessageBuffer(t, e) {
    t.finish(e);
  }
  static finishSizePrefixedMessageBuffer(t, e) {
    t.finish(e, void 0, !0);
  }
  static createMessage(t, e, i, s, r, o) {
    return It.startMessage(t), It.addVersion(t, e), It.addHeaderType(t, i), It.addHeader(t, s), It.addBodyLength(t, r), It.addCustomMetadata(t, o), It.endMessage(t);
  }
};
class Wc extends M {
  visit(t, e) {
    return t == null || e == null ? void 0 : super.visit(t, e);
  }
  visitNull(t, e) {
    return ws.startNull(e), ws.endNull(e);
  }
  visitInt(t, e) {
    return rt.startInt(e), rt.addBitWidth(e, t.bitWidth), rt.addIsSigned(e, t.isSigned), rt.endInt(e);
  }
  visitFloat(t, e) {
    return Ft.startFloatingPoint(e), Ft.addPrecision(e, t.precision), Ft.endFloatingPoint(e);
  }
  visitBinary(t, e) {
    return gs.startBinary(e), gs.endBinary(e);
  }
  visitLargeBinary(t, e) {
    return bs.startLargeBinary(e), bs.endLargeBinary(e);
  }
  visitBool(t, e) {
    return ms.startBool(e), ms.endBool(e);
  }
  visitUtf8(t, e) {
    return Is.startUtf8(e), Is.endUtf8(e);
  }
  visitLargeUtf8(t, e) {
    return _s.startLargeUtf8(e), _s.endLargeUtf8(e);
  }
  visitDecimal(t, e) {
    return le.startDecimal(e), le.addScale(e, t.scale), le.addPrecision(e, t.precision), le.addBitWidth(e, t.bitWidth), le.endDecimal(e);
  }
  visitDate(t, e) {
    return mn.startDate(e), mn.addUnit(e, t.unit), mn.endDate(e);
  }
  visitTime(t, e) {
    return dt.startTime(e), dt.addUnit(e, t.unit), dt.addBitWidth(e, t.bitWidth), dt.endTime(e);
  }
  visitTimestamp(t, e) {
    const i = t.timezone && e.createString(t.timezone) || void 0;
    return ht.startTimestamp(e), ht.addUnit(e, t.unit), i !== void 0 && ht.addTimezone(e, i), ht.endTimestamp(e);
  }
  visitInterval(t, e) {
    return Mt.startInterval(e), Mt.addUnit(e, t.unit), Mt.endInterval(e);
  }
  visitDuration(t, e) {
    return bn.startDuration(e), bn.addUnit(e, t.unit), bn.endDuration(e);
  }
  visitList(t, e) {
    return vs.startList(e), vs.endList(e);
  }
  visitStruct(t, e) {
    return te.startStruct_(e), te.endStruct_(e);
  }
  visitUnion(t, e) {
    X.startTypeIdsVector(e, t.typeIds.length);
    const i = X.createTypeIdsVector(e, t.typeIds);
    return X.startUnion(e), X.addMode(e, t.mode), X.addTypeIds(e, i), X.endUnion(e);
  }
  visitDictionary(t, e) {
    const i = this.visit(t.indices, e);
    return Et.startDictionaryEncoding(e), Et.addId(e, BigInt(t.id)), Et.addIsOrdered(e, t.isOrdered), i !== void 0 && Et.addIndexType(e, i), Et.endDictionaryEncoding(e);
  }
  visitFixedSizeBinary(t, e) {
    return _n.startFixedSizeBinary(e), _n.addByteWidth(e, t.byteWidth), _n.endFixedSizeBinary(e);
  }
  visitFixedSizeList(t, e) {
    return vn.startFixedSizeList(e), vn.addListSize(e, t.listSize), vn.endFixedSizeList(e);
  }
  visitMap(t, e) {
    return wn.startMap(e), wn.addKeysSorted(e, t.keysSorted), wn.endMap(e);
  }
}
const ai = new Wc();
function qc(n, t = /* @__PURE__ */ new Map()) {
  return new C(Kc(n, t), Sn(n.metadata), t);
}
function To(n) {
  return new at(n.count, Lo(n.columns), xo(n.columns), null);
}
function Jc(n) {
  return new Lt(To(n.data), n.id, n.isDelta);
}
function Kc(n, t) {
  return (n.fields || []).filter(Boolean).map((e) => x.fromJSON(e, t));
}
function Ns(n, t) {
  return (n.children || []).filter(Boolean).map((e) => x.fromJSON(e, t));
}
function Lo(n) {
  return (n || []).reduce((t, e) => [
    ...t,
    new Le(e.count, Gc(e.VALIDITY)),
    ...Lo(e.children)
  ], []);
}
function xo(n, t = []) {
  for (let e = -1, i = (n || []).length; ++e < i; ) {
    const s = n[e];
    s.VALIDITY && t.push(new bt(t.length, s.VALIDITY.length)), s.TYPE_ID && t.push(new bt(t.length, s.TYPE_ID.length)), s.OFFSET && t.push(new bt(t.length, s.OFFSET.length)), s.DATA && t.push(new bt(t.length, s.DATA.length)), t = xo(s.children, t);
  }
  return t;
}
function Gc(n) {
  return (n || []).reduce((t, e) => t + +(e === 0), 0);
}
function Zc(n, t) {
  let e, i, s, r, o, a;
  return !t || !(r = n.dictionary) ? (o = Ls(n, Ns(n, t)), s = new x(n.name, o, n.nullable, Sn(n.metadata))) : t.has(e = r.id) ? (i = (i = r.indexType) ? Ts(i) : new Jt(), a = new Kt(t.get(e), i, e, r.isOrdered), s = new x(n.name, a, n.nullable, Sn(n.metadata))) : (i = (i = r.indexType) ? Ts(i) : new Jt(), t.set(e, o = Ls(n, Ns(n, t))), a = new Kt(o, i, e, r.isOrdered), s = new x(n.name, a, n.nullable, Sn(n.metadata))), s || null;
}
function Sn(n = []) {
  return new Map(n.map(({ key: t, value: e }) => [t, e]));
}
function Ts(n) {
  return new et(n.isSigned, n.bitWidth);
}
function Ls(n, t) {
  const e = n.type.name;
  switch (e) {
    case "NONE":
      return new Rt();
    case "null":
      return new Rt();
    case "binary":
      return new On();
    case "largebinary":
      return new Fn();
    case "utf8":
      return new Ze();
    case "largeutf8":
      return new Mn();
    case "bool":
      return new Qe();
    case "list":
      return new De((t || [])[0]);
    case "struct":
      return new q(t || []);
    case "struct_":
      return new q(t || []);
  }
  switch (e) {
    case "int": {
      const i = n.type;
      return new et(i.isSigned, i.bitWidth);
    }
    case "floatingpoint": {
      const i = n.type;
      return new Ae(W[i.precision]);
    }
    case "decimal": {
      const i = n.type;
      return new Nn(i.scale, i.precision, i.bitWidth);
    }
    case "date": {
      const i = n.type;
      return new Tn(ft[i.unit]);
    }
    case "time": {
      const i = n.type;
      return new Ln(b[i.unit], i.bitWidth);
    }
    case "timestamp": {
      const i = n.type;
      return new Xe(b[i.unit], i.timezone);
    }
    case "interval": {
      const i = n.type;
      return new xn(J[i.unit]);
    }
    case "duration": {
      const i = n.type;
      return new Un(b[i.unit]);
    }
    case "union": {
      const i = n.type, [s, ...r] = (i.mode + "").toLowerCase(), o = s.toUpperCase() + r.join("");
      return new tn(tt[o], i.typeIds || [], t || []);
    }
    case "fixedsizebinary": {
      const i = n.type;
      return new Cn(i.byteWidth);
    }
    case "fixedsizelist": {
      const i = n.type;
      return new en(i.listSize, (t || [])[0]);
    }
    case "map": {
      const i = n.type;
      return new nn((t || [])[0], i.keysSorted);
    }
  }
  throw new Error(`Unrecognized type: "${e}"`);
}
var Qc = Gs, Xc = ee;
class mt {
  /** @nocollapse */
  static fromJSON(t, e) {
    const i = new mt(0, $.V5, e);
    return i._createHeader = tl(t, e), i;
  }
  /** @nocollapse */
  static decode(t) {
    t = new Xc(T(t));
    const e = Ht.getRootAsMessage(t), i = e.bodyLength(), s = e.version(), r = e.headerType(), o = new mt(i, s, r);
    return o._createHeader = el(e, r), o;
  }
  /** @nocollapse */
  static encode(t) {
    const e = new Qc();
    let i = -1;
    return t.isSchema() ? i = C.encode(e, t.header()) : t.isRecordBatch() ? i = at.encode(e, t.header()) : t.isDictionaryBatch() && (i = Lt.encode(e, t.header())), Ht.startMessage(e), Ht.addVersion(e, $.V5), Ht.addHeader(e, i), Ht.addHeaderType(e, t.headerType), Ht.addBodyLength(e, BigInt(t.bodyLength)), Ht.finishMessageBuffer(e, Ht.endMessage(e)), e.asUint8Array();
  }
  /** @nocollapse */
  static from(t, e = 0) {
    if (t instanceof C)
      return new mt(0, $.V5, U.Schema, t);
    if (t instanceof at)
      return new mt(e, $.V5, U.RecordBatch, t);
    if (t instanceof Lt)
      return new mt(e, $.V5, U.DictionaryBatch, t);
    throw new Error(`Unrecognized Message header: ${t}`);
  }
  get type() {
    return this.headerType;
  }
  get version() {
    return this._version;
  }
  get headerType() {
    return this._headerType;
  }
  get compression() {
    return this._compression;
  }
  get bodyLength() {
    return this._bodyLength;
  }
  header() {
    return this._createHeader();
  }
  isSchema() {
    return this.headerType === U.Schema;
  }
  isRecordBatch() {
    return this.headerType === U.RecordBatch;
  }
  isDictionaryBatch() {
    return this.headerType === U.DictionaryBatch;
  }
  constructor(t, e, i, s) {
    this._version = e, this._headerType = i, this.body = new Uint8Array(0), this._compression = s?.compression, s && (this._createHeader = () => s), this._bodyLength = P(t);
  }
}
let at = class {
  get nodes() {
    return this._nodes;
  }
  get length() {
    return this._length;
  }
  get buffers() {
    return this._buffers;
  }
  get compression() {
    return this._compression;
  }
  constructor(t, e, i, s) {
    this._nodes = e, this._buffers = i, this._length = P(t), this._compression = s;
  }
};
class Lt {
  get id() {
    return this._id;
  }
  get data() {
    return this._data;
  }
  get isDelta() {
    return this._isDelta;
  }
  get length() {
    return this.data.length;
  }
  get nodes() {
    return this.data.nodes;
  }
  get buffers() {
    return this.data.buffers;
  }
  constructor(t, e, i = !1) {
    this._data = t, this._isDelta = i, this._id = P(e);
  }
}
class bt {
  constructor(t, e) {
    this.offset = P(t), this.length = P(e);
  }
}
class Le {
  constructor(t, e) {
    this.length = P(t), this.nullCount = P(e);
  }
}
class ts {
  constructor(t, e = Je.BUFFER) {
    this.type = t, this.method = e;
  }
}
function tl(n, t) {
  return (() => {
    switch (t) {
      case U.Schema:
        return C.fromJSON(n);
      case U.RecordBatch:
        return at.fromJSON(n);
      case U.DictionaryBatch:
        return Lt.fromJSON(n);
    }
    throw new Error(`Unrecognized Message type: { name: ${U[t]}, type: ${t} }`);
  });
}
function el(n, t) {
  return (() => {
    switch (t) {
      case U.Schema:
        return C.decode(n.header(new Bt()), /* @__PURE__ */ new Map(), n.version());
      case U.RecordBatch:
        return at.decode(n.header(new St()), n.version());
      case U.DictionaryBatch:
        return Lt.decode(n.header(new ae()), n.version());
    }
    throw new Error(`Unrecognized Message type: { name: ${U[t]}, type: ${t} }`);
  });
}
x.encode = hl;
x.decode = ul;
x.fromJSON = Zc;
C.encode = dl;
C.decode = nl;
C.fromJSON = qc;
at.encode = fl;
at.decode = il;
at.fromJSON = To;
Lt.encode = pl;
Lt.decode = sl;
Lt.fromJSON = Jc;
Le.encode = yl;
Le.decode = ol;
bt.encode = gl;
bt.decode = rl;
ts.encode = Co;
ts.decode = Uo;
function nl(n, t = /* @__PURE__ */ new Map(), e = $.V5) {
  const i = ll(n, t);
  return new C(i, Bn(n), t, e);
}
function il(n, t = $.V5) {
  return new at(n.length(), al(n), cl(n, t), Uo(n.compression()));
}
function sl(n, t = $.V5) {
  return new Lt(at.decode(n.data(), t), n.id(), n.isDelta());
}
function rl(n) {
  return new bt(n.offset(), n.length());
}
function ol(n) {
  return new Le(n.length(), n.nullCount());
}
function al(n) {
  const t = [];
  for (let e, i = -1, s = -1, r = n.nodesLength(); ++i < r; )
    (e = n.nodes(i)) && (t[++s] = Le.decode(e));
  return t;
}
function cl(n, t) {
  const e = [];
  for (let i, s = -1, r = -1, o = n.buffersLength(); ++s < o; )
    (i = n.buffers(s)) && (t < $.V4 && (i.bb_pos += 8 * (s + 1)), e[++r] = bt.decode(i));
  return e;
}
function ll(n, t) {
  const e = [];
  for (let i, s = -1, r = -1, o = n.fieldsLength(); ++s < o; )
    (i = n.fields(s)) && (e[++r] = x.decode(i, t));
  return e;
}
function xs(n, t) {
  const e = [];
  for (let i, s = -1, r = -1, o = n.childrenLength(); ++s < o; )
    (i = n.children(s)) && (e[++r] = x.decode(i, t));
  return e;
}
function ul(n, t) {
  let e, i, s, r, o, a;
  return !t || !(a = n.dictionary()) ? (s = Cs(n, xs(n, t)), i = new x(n.name(), s, n.nullable(), Bn(n))) : t.has(e = P(a.id())) ? (r = (r = a.indexType()) ? Us(r) : new Jt(), o = new Kt(t.get(e), r, e, a.isOrdered()), i = new x(n.name(), o, n.nullable(), Bn(n))) : (r = (r = a.indexType()) ? Us(r) : new Jt(), t.set(e, s = Cs(n, xs(n, t))), o = new Kt(s, r, e, a.isOrdered()), i = new x(n.name(), o, n.nullable(), Bn(n))), i || null;
}
function Bn(n) {
  const t = /* @__PURE__ */ new Map();
  if (n)
    for (let e, i, s = -1, r = Math.trunc(n.customMetadataLength()); ++s < r; )
      (e = n.customMetadata(s)) && (i = e.key()) != null && t.set(i, e.value());
  return t;
}
function Us(n) {
  return new et(n.isSigned(), n.bitWidth());
}
function Cs(n, t) {
  const e = n.typeType();
  switch (e) {
    case k.NONE:
      return new Rt();
    case k.Null:
      return new Rt();
    case k.Binary:
      return new On();
    case k.LargeBinary:
      return new Fn();
    case k.Utf8:
      return new Ze();
    case k.LargeUtf8:
      return new Mn();
    case k.Bool:
      return new Qe();
    case k.List:
      return new De((t || [])[0]);
    case k.Struct_:
      return new q(t || []);
  }
  switch (e) {
    case k.Int: {
      const i = n.type(new rt());
      return new et(i.isSigned(), i.bitWidth());
    }
    case k.FloatingPoint: {
      const i = n.type(new Ft());
      return new Ae(i.precision());
    }
    case k.Decimal: {
      const i = n.type(new le());
      return new Nn(i.scale(), i.precision(), i.bitWidth());
    }
    case k.Date: {
      const i = n.type(new mn());
      return new Tn(i.unit());
    }
    case k.Time: {
      const i = n.type(new dt());
      return new Ln(i.unit(), i.bitWidth());
    }
    case k.Timestamp: {
      const i = n.type(new ht());
      return new Xe(i.unit(), i.timezone());
    }
    case k.Interval: {
      const i = n.type(new Mt());
      return new xn(i.unit());
    }
    case k.Duration: {
      const i = n.type(new bn());
      return new Un(i.unit());
    }
    case k.Union: {
      const i = n.type(new X());
      return new tn(i.mode(), i.typeIdsArray() || [], t || []);
    }
    case k.FixedSizeBinary: {
      const i = n.type(new _n());
      return new Cn(i.byteWidth());
    }
    case k.FixedSizeList: {
      const i = n.type(new vn());
      return new en(i.listSize(), (t || [])[0]);
    }
    case k.Map: {
      const i = n.type(new wn());
      return new nn((t || [])[0], i.keysSorted());
    }
  }
  throw new Error(`Unrecognized type: "${k[e]}" (${e})`);
}
function Uo(n) {
  return n ? new ts(n.codec(), n.method()) : null;
}
function dl(n, t) {
  const e = t.fields.map((r) => x.encode(n, r));
  Bt.startFieldsVector(n, e.length);
  const i = Bt.createFieldsVector(n, e), s = t.metadata && t.metadata.size > 0 ? Bt.createCustomMetadataVector(n, [...t.metadata].map(([r, o]) => {
    const a = n.createString(`${r}`), c = n.createString(`${o}`);
    return H.startKeyValue(n), H.addKey(n, a), H.addValue(n, c), H.endKeyValue(n);
  })) : -1;
  return Bt.startSchema(n), Bt.addFields(n, i), Bt.addEndianness(n, ml ? Be.Little : Be.Big), s !== -1 && Bt.addCustomMetadata(n, s), Bt.endSchema(n);
}
function hl(n, t) {
  let e = -1, i = -1, s = -1;
  const r = t.type;
  let o = t.typeId;
  f.isDictionary(r) ? (o = r.dictionary.typeId, s = ai.visit(r, n), i = ai.visit(r.dictionary, n)) : i = ai.visit(r, n);
  const a = (r.children || []).map((d) => x.encode(n, d)), c = lt.createChildrenVector(n, a), u = t.metadata && t.metadata.size > 0 ? lt.createCustomMetadataVector(n, [...t.metadata].map(([d, h]) => {
    const N = n.createString(`${d}`), B = n.createString(`${h}`);
    return H.startKeyValue(n), H.addKey(n, N), H.addValue(n, B), H.endKeyValue(n);
  })) : -1;
  return t.name && (e = n.createString(t.name)), lt.startField(n), lt.addType(n, i), lt.addTypeType(n, o), lt.addChildren(n, c), lt.addNullable(n, !!t.nullable), e !== -1 && lt.addName(n, e), s !== -1 && lt.addDictionary(n, s), u !== -1 && lt.addCustomMetadata(n, u), lt.endField(n);
}
function fl(n, t) {
  const e = t.nodes || [], i = t.buffers || [];
  St.startNodesVector(n, e.length);
  for (const a of e.slice().reverse())
    Le.encode(n, a);
  const s = n.endVector();
  St.startBuffersVector(n, i.length);
  for (const a of i.slice().reverse())
    bt.encode(n, a);
  const r = n.endVector();
  let o = null;
  return t.compression !== null && (o = Co(n, t.compression)), St.startRecordBatch(n), St.addLength(n, BigInt(t.length)), St.addNodes(n, s), St.addBuffers(n, r), t.compression !== null && o && St.addCompression(n, o), St.endRecordBatch(n);
}
function Co(n, t) {
  return Re.startBodyCompression(n), Re.addCodec(n, t.type), Re.addMethod(n, t.method), Re.endBodyCompression(n);
}
function pl(n, t) {
  const e = at.encode(n, t.data);
  return ae.startDictionaryBatch(n), ae.addId(n, BigInt(t.id)), ae.addIsDelta(n, t.isDelta), ae.addData(n, e), ae.endDictionaryBatch(n);
}
function yl(n, t) {
  return Xs.createFieldNode(n, BigInt(t.length), BigInt(t.nullCount));
}
function gl(n, t) {
  return Qs.createBuffer(n, BigInt(t.offset), BigInt(t.length));
}
const ml = (() => {
  const n = new ArrayBuffer(2);
  return new DataView(n).setInt16(
    0,
    256,
    !0
    /* littleEndian */
  ), new Int16Array(n)[0] === 256;
})(), j = Object.freeze({ done: !0, value: void 0 });
class Es {
  constructor(t) {
    this._json = t;
  }
  get schema() {
    return this._json.schema;
  }
  get batches() {
    return this._json.batches || [];
  }
  get dictionaries() {
    return this._json.dictionaries || [];
  }
}
class Eo {
  tee() {
    return this._getDOMStream().tee();
  }
  pipe(t, e) {
    return this._getNodeStream().pipe(t, e);
  }
  pipeTo(t, e) {
    return this._getDOMStream().pipeTo(t, e);
  }
  pipeThrough(t, e) {
    return this._getDOMStream().pipeThrough(t, e);
  }
  _getDOMStream() {
    return this._DOMStream || (this._DOMStream = this.toDOMStream());
  }
  _getNodeStream() {
    return this._nodeStream || (this._nodeStream = this.toNodeStream());
  }
}
class bl extends Eo {
  constructor() {
    super(), this._values = [], this.resolvers = [], this._closedPromise = new Promise((t) => this._closedPromiseResolve = t);
  }
  get closed() {
    return this._closedPromise;
  }
  cancel(t) {
    return O(this, void 0, void 0, function* () {
      yield this.return(t);
    });
  }
  write(t) {
    this._ensureOpen() && (this.resolvers.length <= 0 ? this._values.push(t) : this.resolvers.shift().resolve({ done: !1, value: t }));
  }
  abort(t) {
    this._closedPromiseResolve && (this.resolvers.length <= 0 ? this._error = { error: t } : this.resolvers.shift().reject({ done: !0, value: t }));
  }
  close() {
    if (this._closedPromiseResolve) {
      const { resolvers: t } = this;
      for (; t.length > 0; )
        t.shift().resolve(j);
      this._closedPromiseResolve(), this._closedPromiseResolve = void 0;
    }
  }
  [Symbol.asyncIterator]() {
    return this;
  }
  toDOMStream(t) {
    return ut.toDOMStream(this._closedPromiseResolve || this._error ? this : this._values, t);
  }
  toNodeStream(t) {
    return ut.toNodeStream(this._closedPromiseResolve || this._error ? this : this._values, t);
  }
  throw(t) {
    return O(this, void 0, void 0, function* () {
      return yield this.abort(t), j;
    });
  }
  return(t) {
    return O(this, void 0, void 0, function* () {
      return yield this.close(), j;
    });
  }
  read(t) {
    return O(this, void 0, void 0, function* () {
      return (yield this.next(t, "read")).value;
    });
  }
  peek(t) {
    return O(this, void 0, void 0, function* () {
      return (yield this.next(t, "peek")).value;
    });
  }
  next(...t) {
    return this._values.length > 0 ? Promise.resolve({ done: !1, value: this._values.shift() }) : this._error ? Promise.reject({ done: !0, value: this._error.error }) : this._closedPromiseResolve ? new Promise((e, i) => {
      this.resolvers.push({ resolve: e, reject: i });
    }) : Promise.resolve(j);
  }
  _ensureOpen() {
    if (this._closedPromiseResolve)
      return !0;
    throw new Error("AsyncQueue is closed");
  }
}
class _l extends bl {
  write(t) {
    if ((t = T(t)).byteLength > 0)
      return super.write(t);
  }
  toString(t = !1) {
    return t ? ui(this.toUint8Array(!0)) : this.toUint8Array(!1).then(ui);
  }
  toUint8Array(t = !1) {
    return t ? Tt(this._values)[0] : O(this, void 0, void 0, function* () {
      var e, i, s, r;
      const o = [];
      let a = 0;
      try {
        for (var c = !0, u = _e(this), d; d = yield u.next(), e = d.done, !e; c = !0) {
          r = d.value, c = !1;
          const h = r;
          o.push(h), a += h.byteLength;
        }
      } catch (h) {
        i = { error: h };
      } finally {
        try {
          !c && !e && (s = u.return) && (yield s.call(u));
        } finally {
          if (i) throw i.error;
        }
      }
      return Tt(o, a)[0];
    });
  }
}
class zn {
  constructor(t) {
    t && (this.source = new vl(ut.fromIterable(t)));
  }
  [Symbol.iterator]() {
    return this;
  }
  next(t) {
    return this.source.next(t);
  }
  throw(t) {
    return this.source.throw(t);
  }
  return(t) {
    return this.source.return(t);
  }
  peek(t) {
    return this.source.peek(t);
  }
  read(t) {
    return this.source.read(t);
  }
}
class Fe {
  constructor(t) {
    t instanceof Fe ? this.source = t.source : t instanceof _l ? this.source = new Zt(ut.fromAsyncIterable(t)) : qs(t) ? this.source = new Zt(ut.fromNodeStream(t)) : Ai(t) ? this.source = new Zt(ut.fromDOMStream(t)) : Hs(t) ? this.source = new Zt(ut.fromDOMStream(t.body)) : qn(t) ? this.source = new Zt(ut.fromIterable(t)) : qe(t) ? this.source = new Zt(ut.fromAsyncIterable(t)) : Bi(t) && (this.source = new Zt(ut.fromAsyncIterable(t)));
  }
  [Symbol.asyncIterator]() {
    return this;
  }
  next(t) {
    return this.source.next(t);
  }
  throw(t) {
    return this.source.throw(t);
  }
  return(t) {
    return this.source.return(t);
  }
  get closed() {
    return this.source.closed;
  }
  cancel(t) {
    return this.source.cancel(t);
  }
  peek(t) {
    return this.source.peek(t);
  }
  read(t) {
    return this.source.read(t);
  }
}
class vl {
  constructor(t) {
    this.source = t;
  }
  cancel(t) {
    this.return(t);
  }
  peek(t) {
    return this.next(t, "peek").value;
  }
  read(t) {
    return this.next(t, "read").value;
  }
  next(t, e = "read") {
    return this.source.next({ cmd: e, size: t });
  }
  throw(t) {
    return Object.create(this.source.throw && this.source.throw(t) || j);
  }
  return(t) {
    return Object.create(this.source.return && this.source.return(t) || j);
  }
}
class Zt {
  constructor(t) {
    this.source = t, this._closedPromise = new Promise((e) => this._closedPromiseResolve = e);
  }
  cancel(t) {
    return O(this, void 0, void 0, function* () {
      yield this.return(t);
    });
  }
  get closed() {
    return this._closedPromise;
  }
  read(t) {
    return O(this, void 0, void 0, function* () {
      return (yield this.next(t, "read")).value;
    });
  }
  peek(t) {
    return O(this, void 0, void 0, function* () {
      return (yield this.next(t, "peek")).value;
    });
  }
  next(t) {
    return O(this, arguments, void 0, function* (e, i = "read") {
      return yield this.source.next({ cmd: i, size: e });
    });
  }
  throw(t) {
    return O(this, void 0, void 0, function* () {
      const e = this.source.throw && (yield this.source.throw(t)) || j;
      return this._closedPromiseResolve && this._closedPromiseResolve(), this._closedPromiseResolve = void 0, Object.create(e);
    });
  }
  return(t) {
    return O(this, void 0, void 0, function* () {
      const e = this.source.return && (yield this.source.return(t)) || j;
      return this._closedPromiseResolve && this._closedPromiseResolve(), this._closedPromiseResolve = void 0, Object.create(e);
    });
  }
}
class Vs extends zn {
  constructor(t, e) {
    super(), this.position = 0, this.buffer = T(t), this.size = e === void 0 ? this.buffer.byteLength : e;
  }
  readInt32(t) {
    const { buffer: e, byteOffset: i } = this.readAt(t, 4);
    return new DataView(e, i).getInt32(0, !0);
  }
  seek(t) {
    return this.position = Math.min(t, this.size), t < this.size;
  }
  read(t) {
    const { buffer: e, size: i, position: s } = this;
    return e && s < i ? (typeof t != "number" && (t = Number.POSITIVE_INFINITY), this.position = Math.min(i, s + Math.min(i - s, t)), e.subarray(s, this.position)) : null;
  }
  readAt(t, e) {
    const i = this.buffer, s = Math.min(this.size, t + e);
    return i ? i.subarray(t, s) : new Uint8Array(e);
  }
  close() {
    this.buffer && (this.buffer = null);
  }
  throw(t) {
    return this.close(), { done: !0, value: t };
  }
  return(t) {
    return this.close(), { done: !0, value: t };
  }
}
class kn extends Fe {
  constructor(t, e) {
    super(), this.position = 0, this._handle = t, typeof e == "number" ? this.size = e : this._pending = O(this, void 0, void 0, function* () {
      this.size = (yield t.stat()).size, delete this._pending;
    });
  }
  readInt32(t) {
    return O(this, void 0, void 0, function* () {
      const { buffer: e, byteOffset: i } = yield this.readAt(t, 4);
      return new DataView(e, i).getInt32(0, !0);
    });
  }
  seek(t) {
    return O(this, void 0, void 0, function* () {
      return this._pending && (yield this._pending), this.position = Math.min(t, this.size), t < this.size;
    });
  }
  read(t) {
    return O(this, void 0, void 0, function* () {
      this._pending && (yield this._pending);
      const { _handle: e, size: i, position: s } = this;
      if (e && s < i) {
        typeof t != "number" && (t = Number.POSITIVE_INFINITY);
        let r = s, o = 0, a = 0;
        const c = Math.min(i, r + Math.min(i - r, t)), u = new Uint8Array(Math.max(0, (this.position = c) - r));
        for (; (r += a) < c && (o += a) < u.byteLength; )
          ({ bytesRead: a } = yield e.read(u, o, u.byteLength - o, r));
        return u;
      }
      return null;
    });
  }
  readAt(t, e) {
    return O(this, void 0, void 0, function* () {
      this._pending && (yield this._pending);
      const { _handle: i, size: s } = this;
      if (i && t + e < s) {
        const r = Math.min(s, t + e), o = new Uint8Array(r - t);
        return (yield i.read(o, 0, e, t)).buffer;
      }
      return new Uint8Array(e);
    });
  }
  close() {
    return O(this, void 0, void 0, function* () {
      const t = this._handle;
      this._handle = null, t && (yield t.close());
    });
  }
  throw(t) {
    return O(this, void 0, void 0, function* () {
      return yield this.close(), { done: !0, value: t };
    });
  }
  return(t) {
    return O(this, void 0, void 0, function* () {
      return yield this.close(), { done: !0, value: t };
    });
  }
}
const wl = 65536;
function me(n) {
  return n < 0 && (n = 4294967295 + n + 1), `0x${n.toString(16)}`;
}
const Me = 8, es = [
  1,
  10,
  100,
  1e3,
  1e4,
  1e5,
  1e6,
  1e7,
  1e8
];
class Vo {
  constructor(t) {
    this.buffer = t;
  }
  high() {
    return this.buffer[1];
  }
  low() {
    return this.buffer[0];
  }
  _times(t) {
    const e = new Uint32Array([
      this.buffer[1] >>> 16,
      this.buffer[1] & 65535,
      this.buffer[0] >>> 16,
      this.buffer[0] & 65535
    ]), i = new Uint32Array([
      t.buffer[1] >>> 16,
      t.buffer[1] & 65535,
      t.buffer[0] >>> 16,
      t.buffer[0] & 65535
    ]);
    let s = e[3] * i[3];
    this.buffer[0] = s & 65535;
    let r = s >>> 16;
    return s = e[2] * i[3], r += s, s = e[3] * i[2] >>> 0, r += s, this.buffer[0] += r << 16, this.buffer[1] = r >>> 0 < s ? wl : 0, this.buffer[1] += r >>> 16, this.buffer[1] += e[1] * i[3] + e[2] * i[2] + e[3] * i[1], this.buffer[1] += e[0] * i[3] + e[1] * i[2] + e[2] * i[1] + e[3] * i[0] << 16, this;
  }
  _plus(t) {
    const e = this.buffer[0] + t.buffer[0] >>> 0;
    this.buffer[1] += t.buffer[1], e < this.buffer[0] >>> 0 && ++this.buffer[1], this.buffer[0] = e;
  }
  lessThan(t) {
    return this.buffer[1] < t.buffer[1] || this.buffer[1] === t.buffer[1] && this.buffer[0] < t.buffer[0];
  }
  equals(t) {
    return this.buffer[1] === t.buffer[1] && this.buffer[0] == t.buffer[0];
  }
  greaterThan(t) {
    return t.lessThan(this);
  }
  hex() {
    return `${me(this.buffer[1])} ${me(this.buffer[0])}`;
  }
}
class V extends Vo {
  times(t) {
    return this._times(t), this;
  }
  plus(t) {
    return this._plus(t), this;
  }
  /** @nocollapse */
  static from(t, e = new Uint32Array(2)) {
    return V.fromString(typeof t == "string" ? t : t.toString(), e);
  }
  /** @nocollapse */
  static fromNumber(t, e = new Uint32Array(2)) {
    return V.fromString(t.toString(), e);
  }
  /** @nocollapse */
  static fromString(t, e = new Uint32Array(2)) {
    const i = t.length, s = new V(e);
    for (let r = 0; r < i; ) {
      const o = Me < i - r ? Me : i - r, a = new V(new Uint32Array([Number.parseInt(t.slice(r, r + o), 10), 0])), c = new V(new Uint32Array([es[o], 0]));
      s.times(c), s.plus(a), r += o;
    }
    return s;
  }
  /** @nocollapse */
  static convertArray(t) {
    const e = new Uint32Array(t.length * 2);
    for (let i = -1, s = t.length; ++i < s; )
      V.from(t[i], new Uint32Array(e.buffer, e.byteOffset + 2 * i * 4, 2));
    return e;
  }
  /** @nocollapse */
  static multiply(t, e) {
    return new V(new Uint32Array(t.buffer)).times(e);
  }
  /** @nocollapse */
  static add(t, e) {
    return new V(new Uint32Array(t.buffer)).plus(e);
  }
}
class Q extends Vo {
  negate() {
    return this.buffer[0] = ~this.buffer[0] + 1, this.buffer[1] = ~this.buffer[1], this.buffer[0] == 0 && ++this.buffer[1], this;
  }
  times(t) {
    return this._times(t), this;
  }
  plus(t) {
    return this._plus(t), this;
  }
  lessThan(t) {
    const e = this.buffer[1] << 0, i = t.buffer[1] << 0;
    return e < i || e === i && this.buffer[0] < t.buffer[0];
  }
  /** @nocollapse */
  static from(t, e = new Uint32Array(2)) {
    return Q.fromString(typeof t == "string" ? t : t.toString(), e);
  }
  /** @nocollapse */
  static fromNumber(t, e = new Uint32Array(2)) {
    return Q.fromString(t.toString(), e);
  }
  /** @nocollapse */
  static fromString(t, e = new Uint32Array(2)) {
    const i = t.startsWith("-"), s = t.length, r = new Q(e);
    for (let o = i ? 1 : 0; o < s; ) {
      const a = Me < s - o ? Me : s - o, c = new Q(new Uint32Array([Number.parseInt(t.slice(o, o + a), 10), 0])), u = new Q(new Uint32Array([es[a], 0]));
      r.times(u), r.plus(c), o += a;
    }
    return i ? r.negate() : r;
  }
  /** @nocollapse */
  static convertArray(t) {
    const e = new Uint32Array(t.length * 2);
    for (let i = -1, s = t.length; ++i < s; )
      Q.from(t[i], new Uint32Array(e.buffer, e.byteOffset + 2 * i * 4, 2));
    return e;
  }
  /** @nocollapse */
  static multiply(t, e) {
    return new Q(new Uint32Array(t.buffer)).times(e);
  }
  /** @nocollapse */
  static add(t, e) {
    return new Q(new Uint32Array(t.buffer)).plus(e);
  }
}
class At {
  constructor(t) {
    this.buffer = t;
  }
  high() {
    return new Q(new Uint32Array(this.buffer.buffer, this.buffer.byteOffset + 8, 2));
  }
  low() {
    return new Q(new Uint32Array(this.buffer.buffer, this.buffer.byteOffset, 2));
  }
  negate() {
    return this.buffer[0] = ~this.buffer[0] + 1, this.buffer[1] = ~this.buffer[1], this.buffer[2] = ~this.buffer[2], this.buffer[3] = ~this.buffer[3], this.buffer[0] == 0 && ++this.buffer[1], this.buffer[1] == 0 && ++this.buffer[2], this.buffer[2] == 0 && ++this.buffer[3], this;
  }
  times(t) {
    const e = new V(new Uint32Array([this.buffer[3], 0])), i = new V(new Uint32Array([this.buffer[2], 0])), s = new V(new Uint32Array([this.buffer[1], 0])), r = new V(new Uint32Array([this.buffer[0], 0])), o = new V(new Uint32Array([t.buffer[3], 0])), a = new V(new Uint32Array([t.buffer[2], 0])), c = new V(new Uint32Array([t.buffer[1], 0])), u = new V(new Uint32Array([t.buffer[0], 0]));
    let d = V.multiply(r, u);
    this.buffer[0] = d.low();
    const h = new V(new Uint32Array([d.high(), 0]));
    return d = V.multiply(s, u), h.plus(d), d = V.multiply(r, c), h.plus(d), this.buffer[1] = h.low(), this.buffer[3] = h.lessThan(d) ? 1 : 0, this.buffer[2] = h.high(), new V(new Uint32Array(this.buffer.buffer, this.buffer.byteOffset + 8, 2)).plus(V.multiply(i, u)).plus(V.multiply(s, c)).plus(V.multiply(r, a)), this.buffer[3] += V.multiply(e, u).plus(V.multiply(i, c)).plus(V.multiply(s, a)).plus(V.multiply(r, o)).low(), this;
  }
  plus(t) {
    const e = new Uint32Array(4);
    return e[3] = this.buffer[3] + t.buffer[3] >>> 0, e[2] = this.buffer[2] + t.buffer[2] >>> 0, e[1] = this.buffer[1] + t.buffer[1] >>> 0, e[0] = this.buffer[0] + t.buffer[0] >>> 0, e[0] < this.buffer[0] >>> 0 && ++e[1], e[1] < this.buffer[1] >>> 0 && ++e[2], e[2] < this.buffer[2] >>> 0 && ++e[3], this.buffer[3] = e[3], this.buffer[2] = e[2], this.buffer[1] = e[1], this.buffer[0] = e[0], this;
  }
  hex() {
    return `${me(this.buffer[3])} ${me(this.buffer[2])} ${me(this.buffer[1])} ${me(this.buffer[0])}`;
  }
  /** @nocollapse */
  static multiply(t, e) {
    return new At(new Uint32Array(t.buffer)).times(e);
  }
  /** @nocollapse */
  static add(t, e) {
    return new At(new Uint32Array(t.buffer)).plus(e);
  }
  /** @nocollapse */
  static from(t, e = new Uint32Array(4)) {
    return At.fromString(typeof t == "string" ? t : t.toString(), e);
  }
  /** @nocollapse */
  static fromNumber(t, e = new Uint32Array(4)) {
    return At.fromString(t.toString(), e);
  }
  /** @nocollapse */
  static fromString(t, e = new Uint32Array(4)) {
    const i = t.startsWith("-"), s = t.length, r = new At(e);
    for (let o = i ? 1 : 0; o < s; ) {
      const a = Me < s - o ? Me : s - o, c = new At(new Uint32Array([Number.parseInt(t.slice(o, o + a), 10), 0, 0, 0])), u = new At(new Uint32Array([es[a], 0, 0, 0]));
      r.times(u), r.plus(c), o += a;
    }
    return i ? r.negate() : r;
  }
  /** @nocollapse */
  static convertArray(t) {
    const e = new Uint32Array(t.length * 4);
    for (let i = -1, s = t.length; ++i < s; )
      At.from(t[i], new Uint32Array(e.buffer, e.byteOffset + 16 * i, 4));
    return e;
  }
}
function Il(n) {
  var t, e;
  const i = n.length, s = new Int32Array(i * 2);
  for (let r = 0, o = 0; r < i; r++) {
    const a = n[r];
    s[o++] = (t = a.days) !== null && t !== void 0 ? t : 0, s[o++] = (e = a.milliseconds) !== null && e !== void 0 ? e : 0;
  }
  return s;
}
function Sl(n) {
  var t, e;
  const i = n.length, s = new Int32Array(i * 4);
  for (let r = 0, o = 0; r < i; r++) {
    const a = n[r];
    s[o++] = (t = a.months) !== null && t !== void 0 ? t : 0, s[o++] = (e = a.days) !== null && e !== void 0 ? e : 0;
    const c = a.nanoseconds;
    c ? (s[o++] = Number(BigInt(c) & BigInt(4294967295)), s[o++] = Number(BigInt(c) >> BigInt(32))) : o += 2;
  }
  return s;
}
class ns extends M {
  constructor(t, e, i, s, r = $.V5) {
    super(), this.nodesIndex = -1, this.buffersIndex = -1, this.bytes = t, this.nodes = e, this.buffers = i, this.dictionaries = s, this.metadataVersion = r;
  }
  visit(t) {
    return super.visit(t instanceof x ? t.type : t);
  }
  visitNull(t, { length: e } = this.nextFieldNode()) {
    return I({ type: t, length: e });
  }
  visitBool(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), data: this.readData(t) });
  }
  visitInt(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), data: this.readData(t) });
  }
  visitFloat(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), data: this.readData(t) });
  }
  visitUtf8(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), valueOffsets: this.readOffsets(t), data: this.readData(t) });
  }
  visitLargeUtf8(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), valueOffsets: this.readOffsets(t), data: this.readData(t) });
  }
  visitBinary(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), valueOffsets: this.readOffsets(t), data: this.readData(t) });
  }
  visitLargeBinary(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), valueOffsets: this.readOffsets(t), data: this.readData(t) });
  }
  visitFixedSizeBinary(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), data: this.readData(t) });
  }
  visitDate(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), data: this.readData(t) });
  }
  visitTimestamp(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), data: this.readData(t) });
  }
  visitTime(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), data: this.readData(t) });
  }
  visitDecimal(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), data: this.readData(t) });
  }
  visitList(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), valueOffsets: this.readOffsets(t), child: this.visit(t.children[0]) });
  }
  visitStruct(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), children: this.visitMany(t.children) });
  }
  visitUnion(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return this.metadataVersion < $.V5 && this.readNullBitmap(t, i), t.mode === tt.Sparse ? this.visitSparseUnion(t, { length: e, nullCount: i }) : this.visitDenseUnion(t, { length: e, nullCount: i });
  }
  visitDenseUnion(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, typeIds: this.readTypeIds(t), valueOffsets: this.readOffsets(t), children: this.visitMany(t.children) });
  }
  visitSparseUnion(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, typeIds: this.readTypeIds(t), children: this.visitMany(t.children) });
  }
  visitDictionary(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), data: this.readData(t.indices), dictionary: this.readDictionary(t) });
  }
  visitInterval(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), data: this.readData(t) });
  }
  visitDuration(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), data: this.readData(t) });
  }
  visitFixedSizeList(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), child: this.visit(t.children[0]) });
  }
  visitMap(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), valueOffsets: this.readOffsets(t), child: this.visit(t.children[0]) });
  }
  nextFieldNode() {
    return this.nodes[++this.nodesIndex];
  }
  nextBufferRange() {
    return this.buffers[++this.buffersIndex];
  }
  readNullBitmap(t, e, i = this.nextBufferRange()) {
    return e > 0 && this.readData(t, i) || new Uint8Array(0);
  }
  readOffsets(t, e) {
    return this.readData(t, e);
  }
  readTypeIds(t, e) {
    return this.readData(t, e);
  }
  readData(t, { length: e, offset: i } = this.nextBufferRange()) {
    return this.bytes.subarray(i, i + e);
  }
  readDictionary(t) {
    return this.dictionaries.get(t.id);
  }
}
class Bl extends ns {
  constructor(t, e, i, s, r) {
    super(new Uint8Array(0), e, i, s, r), this.sources = t;
  }
  readNullBitmap(t, e, { offset: i } = this.nextBufferRange()) {
    return e <= 0 ? new Uint8Array(0) : bi(this.sources[i]);
  }
  readOffsets(t, { offset: e } = this.nextBufferRange()) {
    return R(Uint8Array, R(t.OffsetArrayType, this.sources[e]));
  }
  readTypeIds(t, { offset: e } = this.nextBufferRange()) {
    return R(Uint8Array, R(t.ArrayType, this.sources[e]));
  }
  readData(t, { offset: e } = this.nextBufferRange()) {
    const { sources: i } = this;
    if (f.isTimestamp(t))
      return R(Uint8Array, Q.convertArray(i[e]));
    if ((f.isInt(t) || f.isTime(t)) && t.bitWidth === 64 || f.isDuration(t))
      return R(Uint8Array, Q.convertArray(i[e]));
    if (f.isDate(t) && t.unit === ft.MILLISECOND)
      return R(Uint8Array, Q.convertArray(i[e]));
    if (f.isDecimal(t))
      return R(Uint8Array, At.convertArray(i[e]));
    if (f.isBinary(t) || f.isLargeBinary(t) || f.isFixedSizeBinary(t))
      return Al(i[e]);
    if (f.isBool(t))
      return bi(i[e]);
    if (f.isUtf8(t) || f.isLargeUtf8(t))
      return sn(i[e].join(""));
    if (f.isInterval(t))
      switch (t.unit) {
        case J.DAY_TIME:
          return Il(i[e]);
        case J.MONTH_DAY_NANO:
          return Sl(i[e]);
      }
    return R(Uint8Array, R(t.ArrayType, i[e].map((s) => +s)));
  }
}
function Al(n) {
  const t = n.join(""), e = new Uint8Array(t.length / 2);
  for (let i = 0; i < t.length; i += 2)
    e[i >> 1] = Number.parseInt(t.slice(i, i + 2), 16);
  return e;
}
class Dl extends ns {
  constructor(t, e, i, s, r) {
    super(new Uint8Array(0), e, i, s, r), this.bodyChunks = t;
  }
  readData(t, e = this.nextBufferRange()) {
    return this.bodyChunks[this.buffersIndex];
  }
}
class Ro extends Te {
  constructor(t) {
    super(t), this._values = new rn(Uint8Array);
  }
  get byteLength() {
    let t = this._pendingLength + this.length * 4;
    return this._offsets && (t += this._offsets.byteLength), this._values && (t += this._values.byteLength), this._nulls && (t += this._nulls.byteLength), t;
  }
  setValue(t, e) {
    return super.setValue(t, T(e));
  }
  _flushPending(t, e) {
    const i = this._offsets, s = this._values.reserve(e).buffer;
    let r = 0;
    for (const [o, a] of t)
      if (a === void 0)
        i.set(o, 0);
      else {
        const c = a.length;
        s.set(a, r), i.set(o, c), r += c;
      }
  }
}
class zo extends Te {
  constructor(t) {
    super(t), this._values = new rn(Uint8Array);
  }
  get byteLength() {
    let t = this._pendingLength + this.length * 4;
    return this._offsets && (t += this._offsets.byteLength), this._values && (t += this._values.byteLength), this._nulls && (t += this._nulls.byteLength), t;
  }
  setValue(t, e) {
    return super.setValue(t, T(e));
  }
  _flushPending(t, e) {
    const i = this._offsets, s = this._values.reserve(e).buffer;
    let r = 0;
    for (const [o, a] of t)
      if (a === void 0)
        i.set(o, BigInt(0));
      else {
        const c = a.length;
        s.set(a, r), i.set(o, BigInt(c)), r += c;
      }
  }
}
class Ol extends it {
  constructor(t) {
    super(t), this._values = new Mo();
  }
  setValue(t, e) {
    this._values.set(t, +e);
  }
}
class Zn extends Pt {
}
Zn.prototype._setValue = zr;
class ko extends Zn {
}
ko.prototype._setValue = Ni;
class Po extends Zn {
}
Po.prototype._setValue = Ti;
class jo extends Pt {
}
jo.prototype._setValue = jr;
class Fl extends it {
  constructor({ type: t, nullValues: e, dictionaryHashFunction: i }) {
    super({ type: new Kt(t.dictionary, t.indices, t.id, t.isOrdered) }), this._nulls = null, this._dictionaryOffset = 0, this._keysToIndices = /* @__PURE__ */ Object.create(null), this.indices = Pn({ type: this.type.indices, nullValues: e }), this.dictionary = Pn({ type: this.type.dictionary, nullValues: null }), typeof i == "function" && (this.valueToKey = i);
  }
  get values() {
    return this.indices.values;
  }
  get nullCount() {
    return this.indices.nullCount;
  }
  get nullBitmap() {
    return this.indices.nullBitmap;
  }
  get byteLength() {
    return this.indices.byteLength + this.dictionary.byteLength;
  }
  get reservedLength() {
    return this.indices.reservedLength + this.dictionary.reservedLength;
  }
  get reservedByteLength() {
    return this.indices.reservedByteLength + this.dictionary.reservedByteLength;
  }
  isValid(t) {
    return this.indices.isValid(t);
  }
  setValid(t, e) {
    const i = this.indices;
    return e = i.setValid(t, e), this.length = i.length, e;
  }
  setValue(t, e) {
    const i = this._keysToIndices, s = this.valueToKey(e);
    let r = i[s];
    return r === void 0 && (i[s] = r = this._dictionaryOffset + this.dictionary.append(e).length - 1), this.indices.setValue(t, r);
  }
  flush() {
    const t = this.type, e = this._dictionary, i = this.dictionary.toVector(), s = this.indices.flush().clone(t);
    return s.dictionary = e ? e.concat(i) : i, this.finished || (this._dictionaryOffset += i.length), this._dictionary = s.dictionary, this.clear(), s;
  }
  finish() {
    return this.indices.finish(), this.dictionary.finish(), this._dictionaryOffset = 0, this._keysToIndices = /* @__PURE__ */ Object.create(null), super.finish();
  }
  clear() {
    return this.indices.clear(), this.dictionary.clear(), super.clear();
  }
  valueToKey(t) {
    return typeof t == "string" ? t : `${t}`;
  }
}
class $o extends Pt {
}
$o.prototype._setValue = Er;
class Ml extends it {
  setValue(t, e) {
    const [i] = this.children, s = t * this.stride;
    for (let r = -1, o = this.stride; ++r < o; )
      i.set(s + r, e[r]);
  }
  setValid(t, e) {
    return super.setValid(t, e) || this.children[0].setValid((t + 1) * this.stride - 1, !1), e;
  }
  addChild(t, e = "0") {
    if (this.numChildren > 0)
      throw new Error("FixedSizeListBuilder can only have one child.");
    const i = this.children.push(t);
    return this.type = new en(this.type.listSize, new x(e, t.type, !0)), i;
  }
}
class Qn extends Pt {
  setValue(t, e) {
    this._values.set(t, e);
  }
}
class Nl extends Qn {
  setValue(t, e) {
    super.setValue(t, xr(e));
  }
}
class Tl extends Qn {
}
class Ll extends Qn {
}
class an extends Pt {
}
an.prototype._setValue = Hr;
class Yo extends an {
}
Yo.prototype._setValue = ki;
class Ho extends an {
}
Ho.prototype._setValue = Pi;
class Wo extends an {
}
Wo.prototype._setValue = ji;
class xe extends Pt {
}
xe.prototype._setValue = Wr;
class qo extends xe {
}
qo.prototype._setValue = $i;
class Jo extends xe {
}
Jo.prototype._setValue = Yi;
class Ko extends xe {
}
Ko.prototype._setValue = Hi;
class Go extends xe {
}
Go.prototype._setValue = Wi;
class jt extends Pt {
  setValue(t, e) {
    this._values.set(t, e);
  }
}
class xl extends jt {
}
class Ul extends jt {
}
class Cl extends jt {
}
class El extends jt {
}
class Vl extends jt {
}
class Rl extends jt {
}
class zl extends jt {
}
class kl extends jt {
}
class Pl extends Te {
  constructor(t) {
    super(t), this._offsets = new No(t.type);
  }
  addChild(t, e = "0") {
    if (this.numChildren > 0)
      throw new Error("ListBuilder can only have one child.");
    return this.children[this.numChildren] = t, this.type = new De(new x(e, t.type, !0)), this.numChildren - 1;
  }
  _flushPending(t) {
    const e = this._offsets, [i] = this.children;
    for (const [s, r] of t)
      if (typeof r > "u")
        e.set(s, 0);
      else {
        const o = r, a = o.length, c = e.set(s, a).buffer[s];
        for (let u = -1; ++u < a; )
          i.set(c + u, o[u]);
      }
  }
}
class jl extends Te {
  set(t, e) {
    return super.set(t, e);
  }
  setValue(t, e) {
    const i = e instanceof Map ? e : new Map(Object.entries(e)), s = this._pending || (this._pending = /* @__PURE__ */ new Map()), r = s.get(t);
    r && (this._pendingLength -= r.size), this._pendingLength += i.size, s.set(t, i);
  }
  addChild(t, e = `${this.numChildren}`) {
    if (this.numChildren > 0)
      throw new Error("ListBuilder can only have one child.");
    return this.children[this.numChildren] = t, this.type = new nn(new x(e, t.type, !0), this.type.keysSorted), this.numChildren - 1;
  }
  _flushPending(t) {
    const e = this._offsets, [i] = this.children;
    for (const [s, r] of t)
      if (r === void 0)
        e.set(s, 0);
      else {
        let { [s]: o, [s + 1]: a } = e.set(s, r.size).buffer;
        for (const c of r.entries())
          if (i.set(o, c), ++o >= a)
            break;
      }
  }
}
class $l extends it {
  // @ts-ignore
  setValue(t, e) {
  }
  setValid(t, e) {
    return this.length = Math.max(t + 1, this.length), e;
  }
}
class Yl extends it {
  setValue(t, e) {
    const { children: i, type: s } = this;
    switch (Array.isArray(e) || e.constructor) {
      case !0:
        return s.children.forEach((r, o) => i[o].set(t, e[o]));
      case Map:
        return s.children.forEach((r, o) => i[o].set(t, e.get(r.name)));
      default:
        return s.children.forEach((r, o) => i[o].set(t, e[r.name]));
    }
  }
  /** @inheritdoc */
  setValid(t, e) {
    return super.setValid(t, e) || this.children.forEach((i) => i.setValid(t, e)), e;
  }
  addChild(t, e = `${this.numChildren}`) {
    const i = this.children.push(t);
    return this.type = new q([...this.type.children, new x(e, t.type, !0)]), i;
  }
}
class Ue extends Pt {
}
Ue.prototype._setValue = kr;
class Zo extends Ue {
}
Zo.prototype._setValue = Li;
class Qo extends Ue {
}
Qo.prototype._setValue = xi;
class Xo extends Ue {
}
Xo.prototype._setValue = Ui;
class ta extends Ue {
}
ta.prototype._setValue = Ci;
class Ce extends Pt {
}
Ce.prototype._setValue = Pr;
class ea extends Ce {
}
ea.prototype._setValue = Ei;
class na extends Ce {
}
na.prototype._setValue = Vi;
class ia extends Ce {
}
ia.prototype._setValue = Ri;
class sa extends Ce {
}
sa.prototype._setValue = zi;
class is extends it {
  constructor(t) {
    super(t), this._typeIds = new on(Int8Array, 0, 1), typeof t.valueToChildTypeId == "function" && (this._valueToChildTypeId = t.valueToChildTypeId);
  }
  get typeIdToChildIndex() {
    return this.type.typeIdToChildIndex;
  }
  append(t, e) {
    return this.set(this.length, t, e);
  }
  set(t, e, i) {
    return i === void 0 && (i = this._valueToChildTypeId(this, e, t)), this.setValue(t, e, i), this;
  }
  setValue(t, e, i) {
    this._typeIds.set(t, i);
    const s = this.type.typeIdToChildIndex[i], r = this.children[s];
    r?.set(t, e), this.length = Math.max(t + 1, this.length);
  }
  addChild(t, e = `${this.children.length}`) {
    const i = this.children.push(t), { type: { children: s, mode: r, typeIds: o } } = this, a = [...s, new x(e, t.type)];
    return this.type = new tn(r, [...o, i], a), i;
  }
  /** @ignore */
  // @ts-ignore
  _valueToChildTypeId(t, e, i) {
    throw new Error("Cannot map UnionBuilder value to child typeId. Pass the `childTypeId` as the second argument to unionBuilder.append(), or supply a `valueToChildTypeId` function as part of the UnionBuilder constructor options.");
  }
}
class Hl extends is {
}
class Wl extends is {
  constructor(t) {
    super(t), this._offsets = new on(Int32Array);
  }
  /** @ignore */
  setValue(t, e, i) {
    const s = this._typeIds.set(t, i).buffer[t], r = this.getChildAt(this.type.typeIdToChildIndex[s]), o = this._offsets.set(t, r.length).buffer[t];
    r?.set(o, e), this.length = Math.max(t + 1, this.length);
  }
}
class ra extends Te {
  constructor(t) {
    super(t), this._values = new rn(Uint8Array);
  }
  get byteLength() {
    let t = this._pendingLength + this.length * 4;
    return this._offsets && (t += this._offsets.byteLength), this._values && (t += this._values.byteLength), this._nulls && (t += this._nulls.byteLength), t;
  }
  setValue(t, e) {
    return super.setValue(t, sn(e));
  }
  // @ts-ignore
  _flushPending(t, e) {
  }
}
ra.prototype._flushPending = Ro.prototype._flushPending;
class oa extends Te {
  constructor(t) {
    super(t), this._values = new rn(Uint8Array);
  }
  get byteLength() {
    let t = this._pendingLength + this.length * 4;
    return this._offsets && (t += this._offsets.byteLength), this._values && (t += this._values.byteLength), this._nulls && (t += this._nulls.byteLength), t;
  }
  setValue(t, e) {
    return super.setValue(t, sn(e));
  }
  // @ts-ignore
  _flushPending(t, e) {
  }
}
oa.prototype._flushPending = zo.prototype._flushPending;
class ql extends M {
  visitNull() {
    return $l;
  }
  visitBool() {
    return Ol;
  }
  visitInt() {
    return jt;
  }
  visitInt8() {
    return xl;
  }
  visitInt16() {
    return Ul;
  }
  visitInt32() {
    return Cl;
  }
  visitInt64() {
    return El;
  }
  visitUint8() {
    return Vl;
  }
  visitUint16() {
    return Rl;
  }
  visitUint32() {
    return zl;
  }
  visitUint64() {
    return kl;
  }
  visitFloat() {
    return Qn;
  }
  visitFloat16() {
    return Nl;
  }
  visitFloat32() {
    return Tl;
  }
  visitFloat64() {
    return Ll;
  }
  visitUtf8() {
    return ra;
  }
  visitLargeUtf8() {
    return oa;
  }
  visitBinary() {
    return Ro;
  }
  visitLargeBinary() {
    return zo;
  }
  visitFixedSizeBinary() {
    return $o;
  }
  visitDate() {
    return Zn;
  }
  visitDateDay() {
    return ko;
  }
  visitDateMillisecond() {
    return Po;
  }
  visitTimestamp() {
    return Ue;
  }
  visitTimestampSecond() {
    return Zo;
  }
  visitTimestampMillisecond() {
    return Qo;
  }
  visitTimestampMicrosecond() {
    return Xo;
  }
  visitTimestampNanosecond() {
    return ta;
  }
  visitTime() {
    return Ce;
  }
  visitTimeSecond() {
    return ea;
  }
  visitTimeMillisecond() {
    return na;
  }
  visitTimeMicrosecond() {
    return ia;
  }
  visitTimeNanosecond() {
    return sa;
  }
  visitDecimal() {
    return jo;
  }
  visitList() {
    return Pl;
  }
  visitStruct() {
    return Yl;
  }
  visitUnion() {
    return is;
  }
  visitDenseUnion() {
    return Wl;
  }
  visitSparseUnion() {
    return Hl;
  }
  visitDictionary() {
    return Fl;
  }
  visitInterval() {
    return an;
  }
  visitIntervalDayTime() {
    return Yo;
  }
  visitIntervalYearMonth() {
    return Ho;
  }
  visitIntervalMonthDayNano() {
    return Wo;
  }
  visitDuration() {
    return xe;
  }
  visitDurationSecond() {
    return qo;
  }
  visitDurationMillisecond() {
    return Jo;
  }
  visitDurationMicrosecond() {
    return Ko;
  }
  visitDurationNanosecond() {
    return Go;
  }
  visitFixedSizeList() {
    return Ml;
  }
  visitMap() {
    return jl;
  }
}
const Jl = new ql();
class m extends M {
  compareSchemas(t, e) {
    return t === e || e instanceof t.constructor && this.compareManyFields(t.fields, e.fields);
  }
  compareManyFields(t, e) {
    return t === e || Array.isArray(t) && Array.isArray(e) && t.length === e.length && t.every((i, s) => this.compareFields(i, e[s]));
  }
  compareFields(t, e) {
    return t === e || e instanceof t.constructor && t.name === e.name && t.nullable === e.nullable && this.visit(t.type, e.type);
  }
}
function Z(n, t) {
  return t instanceof n.constructor;
}
function se(n, t) {
  return n === t || Z(n, t);
}
function $t(n, t) {
  return n === t || Z(n, t) && n.bitWidth === t.bitWidth && n.isSigned === t.isSigned;
}
function Xn(n, t) {
  return n === t || Z(n, t) && n.precision === t.precision;
}
function Kl(n, t) {
  return n === t || Z(n, t) && n.byteWidth === t.byteWidth;
}
function ss(n, t) {
  return n === t || Z(n, t) && n.unit === t.unit;
}
function cn(n, t) {
  return n === t || Z(n, t) && n.unit === t.unit && n.timezone === t.timezone;
}
function ln(n, t) {
  return n === t || Z(n, t) && n.unit === t.unit && n.bitWidth === t.bitWidth;
}
function Gl(n, t) {
  return n === t || Z(n, t) && n.children.length === t.children.length && zt.compareManyFields(n.children, t.children);
}
function Zl(n, t) {
  return n === t || Z(n, t) && n.children.length === t.children.length && zt.compareManyFields(n.children, t.children);
}
function rs(n, t) {
  return n === t || Z(n, t) && n.mode === t.mode && n.typeIds.every((e, i) => e === t.typeIds[i]) && zt.compareManyFields(n.children, t.children);
}
function Ql(n, t) {
  return n === t || Z(n, t) && n.id === t.id && n.isOrdered === t.isOrdered && zt.visit(n.indices, t.indices) && zt.visit(n.dictionary, t.dictionary);
}
function ti(n, t) {
  return n === t || Z(n, t) && n.unit === t.unit;
}
function un(n, t) {
  return n === t || Z(n, t) && n.unit === t.unit;
}
function Xl(n, t) {
  return n === t || Z(n, t) && n.listSize === t.listSize && n.children.length === t.children.length && zt.compareManyFields(n.children, t.children);
}
function tu(n, t) {
  return n === t || Z(n, t) && n.keysSorted === t.keysSorted && n.children.length === t.children.length && zt.compareManyFields(n.children, t.children);
}
m.prototype.visitNull = se;
m.prototype.visitBool = se;
m.prototype.visitInt = $t;
m.prototype.visitInt8 = $t;
m.prototype.visitInt16 = $t;
m.prototype.visitInt32 = $t;
m.prototype.visitInt64 = $t;
m.prototype.visitUint8 = $t;
m.prototype.visitUint16 = $t;
m.prototype.visitUint32 = $t;
m.prototype.visitUint64 = $t;
m.prototype.visitFloat = Xn;
m.prototype.visitFloat16 = Xn;
m.prototype.visitFloat32 = Xn;
m.prototype.visitFloat64 = Xn;
m.prototype.visitUtf8 = se;
m.prototype.visitLargeUtf8 = se;
m.prototype.visitBinary = se;
m.prototype.visitLargeBinary = se;
m.prototype.visitFixedSizeBinary = Kl;
m.prototype.visitDate = ss;
m.prototype.visitDateDay = ss;
m.prototype.visitDateMillisecond = ss;
m.prototype.visitTimestamp = cn;
m.prototype.visitTimestampSecond = cn;
m.prototype.visitTimestampMillisecond = cn;
m.prototype.visitTimestampMicrosecond = cn;
m.prototype.visitTimestampNanosecond = cn;
m.prototype.visitTime = ln;
m.prototype.visitTimeSecond = ln;
m.prototype.visitTimeMillisecond = ln;
m.prototype.visitTimeMicrosecond = ln;
m.prototype.visitTimeNanosecond = ln;
m.prototype.visitDecimal = se;
m.prototype.visitList = Gl;
m.prototype.visitStruct = Zl;
m.prototype.visitUnion = rs;
m.prototype.visitDenseUnion = rs;
m.prototype.visitSparseUnion = rs;
m.prototype.visitDictionary = Ql;
m.prototype.visitInterval = ti;
m.prototype.visitIntervalDayTime = ti;
m.prototype.visitIntervalYearMonth = ti;
m.prototype.visitIntervalMonthDayNano = ti;
m.prototype.visitDuration = un;
m.prototype.visitDurationSecond = un;
m.prototype.visitDurationMillisecond = un;
m.prototype.visitDurationMicrosecond = un;
m.prototype.visitDurationNanosecond = un;
m.prototype.visitFixedSizeList = Xl;
m.prototype.visitMap = tu;
const zt = new m();
function eu(n, t) {
  return zt.compareSchemas(n, t);
}
function nu(n, t) {
  return zt.visit(n, t);
}
function Pn(n) {
  const t = n.type, e = new (Jl.getVisitFn(t)())(n);
  if (t.children && t.children.length > 0) {
    const i = n.children || [], s = { nullValues: n.nullValues }, r = Array.isArray(i) ? ((o, a) => i[a] || s) : (({ name: o }) => i[o] || s);
    for (const [o, a] of t.children.entries()) {
      const { type: c } = a, u = r(a, o);
      e.children.push(Pn(Object.assign(Object.assign({}, u), { type: c })));
    }
  }
  return e;
}
function ge(n, t) {
  if (n instanceof L || n instanceof D || n.type instanceof f || ArrayBuffer.isView(n))
    return Fo(n);
  const e = { type: t ?? An(n), nullValues: [null] }, i = [...iu(e)(n)], s = i.length === 1 ? i[0] : i.reduce((r, o) => r.concat(o));
  return f.isDictionary(s.type) ? s.memoize() : s;
}
function An(n) {
  if (n.length === 0)
    return new Rt();
  let t = 0, e = 0, i = 0, s = 0, r = 0, o = 0, a = 0, c = 0;
  for (const u of n) {
    if (u == null) {
      ++t;
      continue;
    }
    switch (typeof u) {
      case "bigint":
        ++o;
        continue;
      case "boolean":
        ++a;
        continue;
      case "number":
        ++s;
        continue;
      case "string":
        ++r;
        continue;
      case "object":
        Array.isArray(u) ? ++e : Object.prototype.toString.call(u) === "[object Date]" ? ++c : ++i;
        continue;
    }
    throw new TypeError("Unable to infer Vector type from input values, explicit type declaration expected.");
  }
  if (s + t === n.length)
    return new Jn();
  if (r + t === n.length)
    return new Kt(new Ze(), new Jt());
  if (o + t === n.length)
    return new Fi();
  if (a + t === n.length)
    return new Qe();
  if (c + t === n.length)
    return new ka();
  if (e + t === n.length) {
    const u = n, d = An(u[u.findIndex((h) => h != null)]);
    if (u.every((h) => h == null || nu(d, An(h))))
      return new De(new x("", d, !0));
  } else if (i + t === n.length) {
    const u = /* @__PURE__ */ new Map();
    for (const d of n)
      for (const h of Object.keys(d))
        !u.has(h) && d[h] != null && u.set(h, new x(h, An([d[h]]), !0));
    return new q([...u.values()]);
  }
  throw new TypeError("Unable to infer Vector type from input values, explicit type declaration expected.");
}
function iu(n) {
  const { ["queueingStrategy"]: t = "count" } = n, { ["highWaterMark"]: e = t !== "bytes" ? Number.POSITIVE_INFINITY : Math.pow(2, 14) } = n, i = t !== "bytes" ? "length" : "byteLength";
  return function* (s) {
    let r = 0;
    const o = Pn(n);
    for (const a of s)
      o.append(a)[i] >= e && ++r && (yield o.toVector());
    (o.finish().length > 0 || r === 0) && (yield o.toVector());
  };
}
function ci(n, t) {
  return su(n, t.map((e) => e.data.concat()));
}
function su(n, t) {
  const e = [...n.fields], i = [], s = { numBatches: t.reduce((h, N) => Math.max(h, N.length), 0) };
  let r = 0, o = 0, a = -1;
  const c = t.length;
  let u, d = [];
  for (; s.numBatches-- > 0; ) {
    for (o = Number.POSITIVE_INFINITY, a = -1; ++a < c; )
      d[a] = u = t[a].shift(), o = Math.min(o, u ? u.length : o);
    Number.isFinite(o) && (d = ru(e, o, d, t, s), o > 0 && (i[r++] = I({
      type: new q(e),
      length: o,
      nullCount: 0,
      children: d.slice()
    })));
  }
  return [
    n = n.assign(e),
    i.map((h) => new K(n, h))
  ];
}
function ru(n, t, e, i, s) {
  var r;
  const o = (t + 63 & -64) >> 3;
  for (let a = -1, c = i.length; ++a < c; ) {
    const u = e[a], d = u?.length;
    if (d >= t)
      d === t ? e[a] = u : (e[a] = u.slice(0, t), s.numBatches = Math.max(s.numBatches, i[a].unshift(u.slice(t, d - t))));
    else {
      const h = n[a];
      n[a] = h.clone({ nullable: !0 }), e[a] = (r = u?._changeLengthAndBackfillNullBitmap(t)) !== null && r !== void 0 ? r : I({
        type: h.type,
        length: t,
        nullCount: t,
        nullBitmap: new Uint8Array(o)
      });
    }
  }
  return e;
}
var aa;
class ot {
  constructor(...t) {
    var e, i;
    if (t.length === 0)
      return this.batches = [], this.schema = new C([]), this._offsets = [0], this;
    let s, r;
    t[0] instanceof C && (s = t.shift()), t.at(-1) instanceof Uint32Array && (r = t.pop());
    const o = (c) => {
      if (c) {
        if (c instanceof K)
          return [c];
        if (c instanceof ot)
          return c.batches;
        if (c instanceof L) {
          if (c.type instanceof q)
            return [new K(new C(c.type.children), c)];
        } else {
          if (Array.isArray(c))
            return c.flatMap((u) => o(u));
          if (typeof c[Symbol.iterator] == "function")
            return [...c].flatMap((u) => o(u));
          if (typeof c == "object") {
            const u = Object.keys(c), d = u.map((B) => new D([c[B]])), h = s ?? new C(u.map((B, z) => new x(String(B), d[z].type, d[z].nullable))), [, N] = ci(h, d);
            return N.length === 0 ? [new K(c)] : N;
          }
        }
      }
      return [];
    }, a = t.flatMap((c) => o(c));
    if (s = (i = s ?? ((e = a[0]) === null || e === void 0 ? void 0 : e.schema)) !== null && i !== void 0 ? i : new C([]), !(s instanceof C))
      throw new TypeError("Table constructor expects a [Schema, RecordBatch[]] pair.");
    for (const c of a) {
      if (!(c instanceof K))
        throw new TypeError("Table constructor expects a [Schema, RecordBatch[]] pair.");
      if (!eu(s, c.schema))
        throw new TypeError("Table and inner RecordBatch schemas must be equivalent.");
    }
    this.schema = s, this.batches = a, this._offsets = r ?? vo(this.data);
  }
  /**
   * The contiguous {@link RecordBatch `RecordBatch`} chunks of the Table rows.
   */
  get data() {
    return this.batches.map(({ data: t }) => t);
  }
  /**
   * The number of columns in this Table.
   */
  get numCols() {
    return this.schema.fields.length;
  }
  /**
   * The number of rows in this Table.
   */
  get numRows() {
    return this.data.reduce((t, e) => t + e.length, 0);
  }
  /**
   * The number of null rows in this Table.
   */
  get nullCount() {
    return this._nullCount === -1 && (this._nullCount = _o(this.data)), this._nullCount;
  }
  /**
   * Check whether an element is null.
   *
   * @param index The index at which to read the validity bitmap.
   */
  // @ts-ignore
  isValid(t) {
    return !1;
  }
  /**
   * Get an element value by position.
   *
   * @param index The index of the element to read.
   */
  // @ts-ignore
  get(t) {
    return null;
  }
  /**
    * Get an element value by position.
    * @param index The index of the element to read. A negative index will count back from the last element.
    */
  // @ts-ignore
  at(t) {
    return this.get(Ji(t, this.numRows));
  }
  /**
   * Set an element value by position.
   *
   * @param index The index of the element to write.
   * @param value The value to set.
   */
  // @ts-ignore
  set(t, e) {
  }
  /**
   * Retrieve the index of the first occurrence of a value in an Vector.
   *
   * @param element The value to locate in the Vector.
   * @param offset The index at which to begin the search. If offset is omitted, the search starts at index 0.
   */
  // @ts-ignore
  indexOf(t, e) {
    return -1;
  }
  /**
   * Iterator for rows in this Table.
   */
  [Symbol.iterator]() {
    return this.batches.length > 0 ? Qi.visit(new D(this.data)) : new Array(0)[Symbol.iterator]();
  }
  /**
   * Return a JavaScript Array of the Table rows.
   *
   * @returns An Array of Table rows.
   */
  toArray() {
    return [...this];
  }
  /**
   * Returns a string representation of the Table rows.
   *
   * @returns A string representation of the Table rows.
   */
  toString() {
    return `[
  ${this.toArray().join(`,
  `)}
]`;
  }
  /**
   * Combines two or more Tables of the same schema.
   *
   * @param others Additional Tables to add to the end of this Tables.
   */
  concat(...t) {
    const e = this.schema, i = this.data.concat(t.flatMap(({ data: s }) => s));
    return new ot(e, i.map((s) => new K(e, s)));
  }
  /**
   * Return a zero-copy sub-section of this Table.
   *
   * @param begin The beginning of the specified portion of the Table.
   * @param end The end of the specified portion of the Table. This is exclusive of the element at the index 'end'.
   */
  slice(t, e) {
    const i = this.schema;
    [t, e] = mo({ length: this.numRows }, t, e);
    const s = wo(this.data, this._offsets, t, e);
    return new ot(i, s.map((r) => new K(i, r)));
  }
  /**
   * Returns a child Vector by name, or null if this Vector has no child with the given name.
   *
   * @param name The name of the child to retrieve.
   */
  getChild(t) {
    return this.getChildAt(this.schema.fields.findIndex((e) => e.name === t));
  }
  /**
   * Returns a child Vector by index, or null if this Vector has no child at the supplied index.
   *
   * @param index The index of the child to retrieve.
   */
  getChildAt(t) {
    if (t > -1 && t < this.schema.fields.length) {
      const e = this.data.map((i) => i.children[t]);
      if (e.length === 0) {
        const { type: i } = this.schema.fields[t], s = I({ type: i, length: 0, nullCount: 0 });
        e.push(s._changeLengthAndBackfillNullBitmap(this.numRows));
      }
      return new D(e);
    }
    return null;
  }
  /**
   * Sets a child Vector by name.
   *
   * @param name The name of the child to overwrite.
   * @returns A new Table with the supplied child for the specified name.
   */
  setChild(t, e) {
    var i;
    return this.setChildAt((i = this.schema.fields) === null || i === void 0 ? void 0 : i.findIndex((s) => s.name === t), e);
  }
  setChildAt(t, e) {
    let i = this.schema, s = [...this.batches];
    if (t > -1 && t < this.numCols) {
      e || (e = new D([I({ type: new Rt(), length: this.numRows })]));
      const r = i.fields.slice(), o = r[t].clone({ type: e.type }), a = this.schema.fields.map((c, u) => this.getChildAt(u));
      [r[t], a[t]] = [o, e], [i, s] = ci(i, a);
    }
    return new ot(i, s);
  }
  /**
   * Construct a new Table containing only specified columns.
   *
   * @param columnNames Names of columns to keep.
   * @returns A new Table of columns matching the specified names.
   */
  select(t) {
    const e = this.schema.fields.reduce((i, s, r) => i.set(s.name, r), /* @__PURE__ */ new Map());
    return this.selectAt(t.map((i) => e.get(i)).filter((i) => i > -1));
  }
  /**
   * Construct a new Table containing only columns at the specified indices.
   *
   * @param columnIndices Indices of columns to keep.
   * @returns A new Table of columns at the specified indices.
   */
  selectAt(t) {
    const e = this.schema.selectAt(t), i = this.batches.map((s) => s.selectAt(t));
    return new ot(e, i);
  }
  assign(t) {
    const e = this.schema.fields, [i, s] = t.schema.fields.reduce((a, c, u) => {
      const [d, h] = a, N = e.findIndex((B) => B.name === c.name);
      return ~N ? h[N] = u : d.push(u), a;
    }, [[], []]), r = this.schema.assign(t.schema), o = [
      ...e.map((a, c) => [c, s[c]]).map(([a, c]) => c === void 0 ? this.getChildAt(a) : t.getChildAt(c)),
      ...i.map((a) => t.getChildAt(a))
    ].filter(Boolean);
    return new ot(...ci(r, o));
  }
}
aa = Symbol.toStringTag;
ot[aa] = ((n) => (n.schema = null, n.batches = [], n._offsets = new Uint32Array([0]), n._nullCount = -1, n[Symbol.isConcatSpreadable] = !0, n.isValid = En(Zi), n.get = En(nt.getVisitFn(l.Struct)), n.set = Io(pt.getVisitFn(l.Struct)), n.indexOf = So(Vn.getVisitFn(l.Struct)), "Table"))(ot.prototype);
function ou(n) {
  const t = {}, e = Object.entries(n);
  for (const [i, s] of e)
    t[i] = ge(s);
  return new ot(t);
}
var ca;
class K {
  constructor(...t) {
    switch (t.length) {
      case 2: {
        if ([this.schema] = t, !(this.schema instanceof C))
          throw new TypeError("RecordBatch constructor expects a [Schema, Data] pair.");
        if ([
          ,
          this.data = I({
            nullCount: 0,
            type: new q(this.schema.fields),
            children: this.schema.fields.map((e) => I({ type: e.type, nullCount: 0 }))
          })
        ] = t, !(this.data instanceof L))
          throw new TypeError("RecordBatch constructor expects a [Schema, Data] pair.");
        [this.schema, this.data] = Rs(this.schema, this.data.children);
        break;
      }
      case 1: {
        const [e] = t, { fields: i, children: s, length: r } = Object.keys(e).reduce((c, u, d) => (c.children[d] = e[u], c.length = Math.max(c.length, e[u].length), c.fields[d] = x.new({ name: u, type: e[u].type, nullable: !0 }), c), {
          length: 0,
          fields: new Array(),
          children: new Array()
        }), o = new C(i), a = I({ type: new q(i), length: r, children: s, nullCount: 0 });
        [this.schema, this.data] = Rs(o, a.children, r);
        break;
      }
      default:
        throw new TypeError("RecordBatch constructor expects an Object mapping names to child Data, or a [Schema, Data] pair.");
    }
  }
  get dictionaries() {
    return this._dictionaries || (this._dictionaries = la(this.schema.fields, this.data.children));
  }
  /**
   * The number of columns in this RecordBatch.
   */
  get numCols() {
    return this.schema.fields.length;
  }
  /**
   * The number of rows in this RecordBatch.
   */
  get numRows() {
    return this.data.length;
  }
  /**
   * The number of null rows in this RecordBatch.
   */
  get nullCount() {
    return this.data.nullCount;
  }
  /**
   * Check whether an row is null.
   * @param index The index at which to read the validity bitmap.
   */
  isValid(t) {
    return this.data.getValid(t);
  }
  /**
   * Get a row by position.
   * @param index The index of the row to read.
   */
  get(t) {
    return nt.visit(this.data, t);
  }
  /**
    * Get a row value by position.
    * @param index The index of the row to read. A negative index will count back from the last row.
    */
  at(t) {
    return this.get(Ji(t, this.numRows));
  }
  /**
   * Set a row by position.
   * @param index The index of the row to write.
   * @param value The value to set.
   */
  set(t, e) {
    return pt.visit(this.data, t, e);
  }
  /**
   * Retrieve the index of the first occurrence of a row in an RecordBatch.
   * @param element The row to locate in the RecordBatch.
   * @param offset The index at which to begin the search. If offset is omitted, the search starts at index 0.
   */
  indexOf(t, e) {
    return Vn.visit(this.data, t, e);
  }
  /**
   * Iterator for rows in this RecordBatch.
   */
  [Symbol.iterator]() {
    return Qi.visit(new D([this.data]));
  }
  /**
   * Return a JavaScript Array of the RecordBatch rows.
   * @returns An Array of RecordBatch rows.
   */
  toArray() {
    return [...this];
  }
  /**
   * Combines two or more RecordBatch of the same schema.
   * @param others Additional RecordBatch to add to the end of this RecordBatch.
   */
  concat(...t) {
    return new ot(this.schema, [this, ...t]);
  }
  /**
   * Return a zero-copy sub-section of this RecordBatch.
   * @param start The beginning of the specified portion of the RecordBatch.
   * @param end The end of the specified portion of the RecordBatch. This is exclusive of the row at the index 'end'.
   */
  slice(t, e) {
    const [i] = new D([this.data]).slice(t, e).data;
    return new K(this.schema, i);
  }
  /**
   * Returns a child Vector by name, or null if this Vector has no child with the given name.
   * @param name The name of the child to retrieve.
   */
  getChild(t) {
    var e;
    return this.getChildAt((e = this.schema.fields) === null || e === void 0 ? void 0 : e.findIndex((i) => i.name === t));
  }
  /**
   * Returns a child Vector by index, or null if this Vector has no child at the supplied index.
   * @param index The index of the child to retrieve.
   */
  getChildAt(t) {
    return t > -1 && t < this.schema.fields.length ? new D([this.data.children[t]]) : null;
  }
  /**
   * Sets a child Vector by name.
   * @param name The name of the child to overwrite.
   * @returns A new RecordBatch with the new child for the specified name.
   */
  setChild(t, e) {
    var i;
    return this.setChildAt((i = this.schema.fields) === null || i === void 0 ? void 0 : i.findIndex((s) => s.name === t), e);
  }
  setChildAt(t, e) {
    let i = this.schema, s = this.data;
    if (t > -1 && t < this.numCols) {
      e || (e = new D([I({ type: new Rt(), length: this.numRows })]));
      const r = i.fields.slice(), o = s.children.slice(), a = r[t].clone({ type: e.type });
      [r[t], o[t]] = [a, e.data[0]], i = new C(r, new Map(this.schema.metadata)), s = I({ type: new q(r), children: o });
    }
    return new K(i, s);
  }
  /**
   * Construct a new RecordBatch containing only specified columns.
   *
   * @param columnNames Names of columns to keep.
   * @returns A new RecordBatch of columns matching the specified names.
   */
  select(t) {
    const e = this.schema.select(t), i = new q(e.fields), s = [];
    for (const r of t) {
      const o = this.schema.fields.findIndex((a) => a.name === r);
      ~o && (s[o] = this.data.children[o]);
    }
    return new K(e, I({ type: i, length: this.numRows, children: s }));
  }
  /**
   * Construct a new RecordBatch containing only columns at the specified indices.
   *
   * @param columnIndices Indices of columns to keep.
   * @returns A new RecordBatch of columns matching at the specified indices.
   */
  selectAt(t) {
    const e = this.schema.selectAt(t), i = t.map((r) => this.data.children[r]).filter(Boolean), s = I({ type: new q(e.fields), length: this.numRows, children: i });
    return new K(e, s);
  }
}
ca = Symbol.toStringTag;
K[ca] = ((n) => (n._nullCount = -1, n[Symbol.isConcatSpreadable] = !0, "RecordBatch"))(K.prototype);
function Rs(n, t, e = t.reduce((i, s) => Math.max(i, s.length), 0)) {
  var i;
  const s = [...n.fields], r = [...t], o = (e + 63 & -64) >> 3;
  for (const [a, c] of n.fields.entries()) {
    const u = t[a];
    (!u || u.length !== e) && (s[a] = c.clone({ nullable: !0 }), r[a] = (i = u?._changeLengthAndBackfillNullBitmap(e)) !== null && i !== void 0 ? i : I({
      type: c.type,
      length: e,
      nullCount: e,
      nullBitmap: new Uint8Array(o)
    }));
  }
  return [
    n.assign(s),
    I({ type: new q(s), length: e, children: r })
  ];
}
function la(n, t, e = /* @__PURE__ */ new Map()) {
  var i, s;
  if (((i = n?.length) !== null && i !== void 0 ? i : 0) > 0 && n?.length === t?.length)
    for (let r = -1, o = n.length; ++r < o; ) {
      const { type: a } = n[r], c = t[r];
      for (const u of [c, ...((s = c?.dictionary) === null || s === void 0 ? void 0 : s.data) || []])
        la(a.children, u?.children, e);
      if (f.isDictionary(a)) {
        const { id: u } = a;
        if (!e.has(u))
          c?.dictionary && e.set(u, c.dictionary);
        else if (e.get(u) !== c.dictionary)
          throw new Error("Cannot create Schema containing two different dictionaries with the same Id");
      }
    }
  return e;
}
class ua extends K {
  constructor(t) {
    const e = t.fields.map((s) => I({ type: s.type })), i = I({ type: new q(t.fields), nullCount: 0, children: e });
    super(t, i);
  }
}
const os = (n) => `Expected ${U[n]} Message in stream, but was null or length 0.`, as = (n) => `Header pointer of flatbuffer-encoded ${U[n]} Message is null or length 0.`, da = (n, t) => `Expected to read ${n} metadata bytes, but only read ${t}.`, ha = (n, t) => `Expected to read ${n} bytes for message body, but only read ${t}.`;
class fa {
  constructor(t) {
    this.source = t instanceof zn ? t : new zn(t);
  }
  [Symbol.iterator]() {
    return this;
  }
  next() {
    let t;
    return (t = this.readMetadataLength()).done || t.value === -1 && (t = this.readMetadataLength()).done || (t = this.readMetadata(t.value)).done ? j : t;
  }
  throw(t) {
    return this.source.throw(t);
  }
  return(t) {
    return this.source.return(t);
  }
  readMessage(t) {
    let e;
    if ((e = this.next()).done)
      return null;
    if (t != null && e.value.headerType !== t)
      throw new Error(os(t));
    return e.value;
  }
  readMessageBody(t) {
    if (t <= 0)
      return new Uint8Array(0);
    const e = T(this.source.read(t));
    if (e.byteLength < t)
      throw new Error(ha(t, e.byteLength));
    return (
      /* 1. */
      e.byteOffset % 8 === 0 && /* 2. */
      e.byteOffset + e.byteLength <= e.buffer.byteLength ? e : e.slice()
    );
  }
  readSchema(t = !1) {
    const e = U.Schema, i = this.readMessage(e), s = i?.header();
    if (t && !s)
      throw new Error(as(e));
    return s;
  }
  readMetadataLength() {
    const t = this.source.read(ei), e = t && new ee(t), i = e?.readInt32(0) || 0;
    return { done: i === 0, value: i };
  }
  readMetadata(t) {
    const e = this.source.read(t);
    if (!e)
      return j;
    if (e.byteLength < t)
      throw new Error(da(t, e.byteLength));
    return { done: !1, value: mt.decode(e) };
  }
}
class au {
  constructor(t, e) {
    this.source = t instanceof Fe ? t : Ys(t) ? new kn(t, e) : new Fe(t);
  }
  [Symbol.asyncIterator]() {
    return this;
  }
  next() {
    return O(this, void 0, void 0, function* () {
      let t;
      return (t = yield this.readMetadataLength()).done || t.value === -1 && (t = yield this.readMetadataLength()).done || (t = yield this.readMetadata(t.value)).done ? j : t;
    });
  }
  throw(t) {
    return O(this, void 0, void 0, function* () {
      return yield this.source.throw(t);
    });
  }
  return(t) {
    return O(this, void 0, void 0, function* () {
      return yield this.source.return(t);
    });
  }
  readMessage(t) {
    return O(this, void 0, void 0, function* () {
      let e;
      if ((e = yield this.next()).done)
        return null;
      if (t != null && e.value.headerType !== t)
        throw new Error(os(t));
      return e.value;
    });
  }
  readMessageBody(t) {
    return O(this, void 0, void 0, function* () {
      if (t <= 0)
        return new Uint8Array(0);
      const e = T(yield this.source.read(t));
      if (e.byteLength < t)
        throw new Error(ha(t, e.byteLength));
      return (
        /* 1. */
        e.byteOffset % 8 === 0 && /* 2. */
        e.byteOffset + e.byteLength <= e.buffer.byteLength ? e : e.slice()
      );
    });
  }
  readSchema() {
    return O(this, arguments, void 0, function* (t = !1) {
      const e = U.Schema, i = yield this.readMessage(e), s = i?.header();
      if (t && !s)
        throw new Error(as(e));
      return s;
    });
  }
  readMetadataLength() {
    return O(this, void 0, void 0, function* () {
      const t = yield this.source.read(ei), e = t && new ee(t), i = e?.readInt32(0) || 0;
      return { done: i === 0, value: i };
    });
  }
  readMetadata(t) {
    return O(this, void 0, void 0, function* () {
      const e = yield this.source.read(t);
      if (!e)
        return j;
      if (e.byteLength < t)
        throw new Error(da(t, e.byteLength));
      return { done: !1, value: mt.decode(e) };
    });
  }
}
class cu extends fa {
  constructor(t) {
    super(new Uint8Array(0)), this._schema = !1, this._body = [], this._batchIndex = 0, this._dictionaryIndex = 0, this._json = t instanceof Es ? t : new Es(t);
  }
  next() {
    const { _json: t } = this;
    if (!this._schema)
      return this._schema = !0, { done: !1, value: mt.fromJSON(t.schema, U.Schema) };
    if (this._dictionaryIndex < t.dictionaries.length) {
      const e = t.dictionaries[this._dictionaryIndex++];
      return this._body = e.data.columns, { done: !1, value: mt.fromJSON(e, U.DictionaryBatch) };
    }
    if (this._batchIndex < t.batches.length) {
      const e = t.batches[this._batchIndex++];
      return this._body = e.columns, { done: !1, value: mt.fromJSON(e, U.RecordBatch) };
    }
    return this._body = [], j;
  }
  readMessageBody(t) {
    return e(this._body);
    function e(i) {
      return (i || []).reduce((s, r) => [
        ...s,
        ...r.VALIDITY && [r.VALIDITY] || [],
        ...r.TYPE_ID && [r.TYPE_ID] || [],
        ...r.OFFSET && [r.OFFSET] || [],
        ...r.DATA && [r.DATA] || [],
        ...e(r.children)
      ], []);
    }
  }
  readMessage(t) {
    let e;
    if ((e = this.next()).done)
      return null;
    if (t != null && e.value.headerType !== t)
      throw new Error(os(t));
    return e.value;
  }
  readSchema() {
    const t = U.Schema, e = this.readMessage(t), i = e?.header();
    if (!e || !i)
      throw new Error(as(t));
    return i;
  }
}
const ei = 4, Ii = "ARROW1", jn = new Uint8Array(Ii.length);
for (let n = 0; n < Ii.length; n += 1)
  jn[n] = Ii.codePointAt(n);
function cs(n, t = 0) {
  for (let e = -1, i = jn.length; ++e < i; )
    if (jn[e] !== n[t + e])
      return !1;
  return !0;
}
const dn = jn.length, pa = dn + ei, lu = dn * 2 + ei;
class uu {
  constructor() {
    this.LZ4_FRAME_MAGIC = new Uint8Array([4, 34, 77, 24]), this.MIN_HEADER_LENGTH = 7;
  }
  isValidCodecEncode(t) {
    const e = new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8]), i = t.encode(e);
    return this._isValidCompressed(i);
  }
  _isValidCompressed(t) {
    return this._hasMinimumLength(t) && this._hasValidMagicNumber(t) && this._hasValidVersion(t);
  }
  _hasMinimumLength(t) {
    return t.length >= this.MIN_HEADER_LENGTH;
  }
  _hasValidMagicNumber(t) {
    return this.LZ4_FRAME_MAGIC.every((e, i) => t[i] === e);
  }
  _hasValidVersion(t) {
    return (t[4] & 192) >> 6 === 1;
  }
}
class du {
  constructor() {
    this.ZSTD_MAGIC = new Uint8Array([40, 181, 47, 253]), this.MIN_HEADER_LENGTH = 6;
  }
  isValidCodecEncode(t) {
    const e = new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8]), i = t.encode(e);
    return this._isValidCompressed(i);
  }
  _isValidCompressed(t) {
    return this._hasMinimumLength(t) && this._hasValidMagicNumber(t);
  }
  _hasMinimumLength(t) {
    return t.length >= this.MIN_HEADER_LENGTH;
  }
  _hasValidMagicNumber(t) {
    return this.ZSTD_MAGIC.every((e, i) => t[i] === e);
  }
}
const hu = {
  [ne.LZ4_FRAME]: new uu(),
  [ne.ZSTD]: new du()
};
class fu {
  constructor() {
    this.registry = {};
  }
  set(t, e) {
    if (e?.encode && typeof e.encode == "function" && !hu[t].isValidCodecEncode(e))
      throw new Error(`Encoder for ${ne[t]} is not valid.`);
    this.registry[t] = e;
  }
  get(t) {
    var e;
    return ((e = this.registry) === null || e === void 0 ? void 0 : e[t]) || null;
  }
}
const zs = new fu(), pu = -1, yu = 8;
class Vt extends Eo {
  constructor(t) {
    super(), this._impl = t;
  }
  get closed() {
    return this._impl.closed;
  }
  get schema() {
    return this._impl.schema;
  }
  get autoDestroy() {
    return this._impl.autoDestroy;
  }
  get dictionaries() {
    return this._impl.dictionaries;
  }
  get numDictionaries() {
    return this._impl.numDictionaries;
  }
  get numRecordBatches() {
    return this._impl.numRecordBatches;
  }
  get footer() {
    return this._impl.isFile() ? this._impl.footer : null;
  }
  isSync() {
    return this._impl.isSync();
  }
  isAsync() {
    return this._impl.isAsync();
  }
  isFile() {
    return this._impl.isFile();
  }
  isStream() {
    return this._impl.isStream();
  }
  next() {
    return this._impl.next();
  }
  throw(t) {
    return this._impl.throw(t);
  }
  return(t) {
    return this._impl.return(t);
  }
  cancel() {
    return this._impl.cancel();
  }
  reset(t) {
    return this._impl.reset(t), this._DOMStream = void 0, this._nodeStream = void 0, this;
  }
  open(t) {
    const e = this._impl.open(t);
    return qe(e) ? e.then(() => this) : this;
  }
  readRecordBatch(t) {
    return this._impl.isFile() ? this._impl.readRecordBatch(t) : null;
  }
  [Symbol.iterator]() {
    return this._impl[Symbol.iterator]();
  }
  [Symbol.asyncIterator]() {
    return this._impl[Symbol.asyncIterator]();
  }
  toDOMStream() {
    return ut.toDOMStream(this.isSync() ? { [Symbol.iterator]: () => this } : { [Symbol.asyncIterator]: () => this });
  }
  toNodeStream() {
    return ut.toNodeStream(this.isSync() ? { [Symbol.iterator]: () => this } : { [Symbol.asyncIterator]: () => this }, { objectMode: !0 });
  }
  /** @nocollapse */
  // @ts-ignore
  static throughNode(t) {
    throw new Error('"throughNode" not available in this environment');
  }
  /** @nocollapse */
  static throughDOM(t, e) {
    throw new Error('"throughDOM" not available in this environment');
  }
  /** @nocollapse */
  static from(t) {
    return t instanceof Vt ? t : di(t) ? _u(t) : Ys(t) ? Iu(t) : qe(t) ? O(this, void 0, void 0, function* () {
      return yield Vt.from(yield t);
    }) : Hs(t) || Ai(t) || qs(t) || Bi(t) ? wu(new Fe(t)) : vu(new zn(t));
  }
  /** @nocollapse */
  static readAll(t) {
    return t instanceof Vt ? t.isSync() ? ks(t) : Ps(t) : di(t) || ArrayBuffer.isView(t) || qn(t) || $s(t) ? ks(t) : Ps(t);
  }
}
class $n extends Vt {
  constructor(t) {
    super(t), this._impl = t;
  }
  readAll() {
    return [...this];
  }
  [Symbol.iterator]() {
    return this._impl[Symbol.iterator]();
  }
  [Symbol.asyncIterator]() {
    return Nt(this, arguments, function* () {
      yield F(yield* gn(_e(this[Symbol.iterator]())));
    });
  }
}
class Yn extends Vt {
  constructor(t) {
    super(t), this._impl = t;
  }
  readAll() {
    return O(this, void 0, void 0, function* () {
      var t, e, i, s;
      const r = new Array();
      try {
        for (var o = !0, a = _e(this), c; c = yield a.next(), t = c.done, !t; o = !0) {
          s = c.value, o = !1;
          const u = s;
          r.push(u);
        }
      } catch (u) {
        e = { error: u };
      } finally {
        try {
          !o && !t && (i = a.return) && (yield i.call(a));
        } finally {
          if (e) throw e.error;
        }
      }
      return r;
    });
  }
  [Symbol.iterator]() {
    throw new Error("AsyncRecordBatchStreamReader is not Iterable");
  }
  [Symbol.asyncIterator]() {
    return this._impl[Symbol.asyncIterator]();
  }
}
class ya extends $n {
  constructor(t) {
    super(t), this._impl = t;
  }
}
class gu extends Yn {
  constructor(t) {
    super(t), this._impl = t;
  }
}
class ga {
  get numDictionaries() {
    return this._dictionaryIndex;
  }
  get numRecordBatches() {
    return this._recordBatchIndex;
  }
  constructor(t = /* @__PURE__ */ new Map()) {
    this.closed = !1, this.autoDestroy = !0, this._dictionaryIndex = 0, this._recordBatchIndex = 0, this.dictionaries = t;
  }
  isSync() {
    return !1;
  }
  isAsync() {
    return !1;
  }
  isFile() {
    return !1;
  }
  isStream() {
    return !1;
  }
  reset(t) {
    return this._dictionaryIndex = 0, this._recordBatchIndex = 0, this.schema = t, this.dictionaries = /* @__PURE__ */ new Map(), this;
  }
  _loadRecordBatch(t, e) {
    let i;
    if (t.compression != null) {
      const r = zs.get(t.compression.type);
      if (r?.decode && typeof r.decode == "function") {
        const { decommpressedBody: o, buffers: a } = this._decompressBuffers(t, e, r);
        i = this._loadCompressedVectors(t, o, this.schema.fields), t = new at(t.length, t.nodes, a, null);
      } else
        throw new Error("Record batch is compressed but codec not found");
    } else
      i = this._loadVectors(t, e, this.schema.fields);
    const s = I({ type: new q(this.schema.fields), length: t.length, children: i });
    return new K(this.schema, s);
  }
  _loadDictionaryBatch(t, e) {
    const { id: i, isDelta: s } = t, { dictionaries: r, schema: o } = this, a = r.get(i), c = o.dictionaries.get(i);
    let u;
    if (t.data.compression != null) {
      const d = zs.get(t.data.compression.type);
      if (d?.decode && typeof d.decode == "function") {
        const { decommpressedBody: h, buffers: N } = this._decompressBuffers(t.data, e, d);
        u = this._loadCompressedVectors(t.data, h, [c]), t = new Lt(new at(t.data.length, t.data.nodes, N, null), i, s);
      } else
        throw new Error("Dictionary batch is compressed but codec not found");
    } else
      u = this._loadVectors(t.data, e, [c]);
    return (a && s ? a.concat(new D(u)) : new D(u)).memoize();
  }
  _loadVectors(t, e, i) {
    return new ns(e, t.nodes, t.buffers, this.dictionaries, this.schema.metadataVersion).visitMany(i);
  }
  _loadCompressedVectors(t, e, i) {
    return new Dl(e, t.nodes, t.buffers, this.dictionaries, this.schema.metadataVersion).visitMany(i);
  }
  _decompressBuffers(t, e, i) {
    const s = [], r = [];
    let o = 0;
    for (const { offset: a, length: c } of t.buffers) {
      if (c === 0) {
        s.push(new Uint8Array(0)), r.push(new bt(o, 0));
        continue;
      }
      const u = new ee(e.subarray(a, a + c)), d = P(u.readInt64(0)), h = u.bytes().subarray(yu), N = d === pu ? h : i.decode(h);
      s.push(N);
      const B = (o + 7 & -8) - o;
      o += B, r.push(new bt(o, N.length)), o += N.length;
    }
    return {
      decommpressedBody: s,
      buffers: r
    };
  }
}
class Hn extends ga {
  constructor(t, e) {
    super(e), this._reader = di(t) ? new cu(this._handle = t) : new fa(this._handle = t);
  }
  isSync() {
    return !0;
  }
  isStream() {
    return !0;
  }
  [Symbol.iterator]() {
    return this;
  }
  cancel() {
    !this.closed && (this.closed = !0) && (this.reset()._reader.return(), this._reader = null, this.dictionaries = null);
  }
  open(t) {
    return this.closed || (this.autoDestroy = ba(this, t), this.schema || (this.schema = this._reader.readSchema()) || this.cancel()), this;
  }
  throw(t) {
    return !this.closed && this.autoDestroy && (this.closed = !0) ? this.reset()._reader.throw(t) : j;
  }
  return(t) {
    return !this.closed && this.autoDestroy && (this.closed = !0) ? this.reset()._reader.return(t) : j;
  }
  next() {
    if (this.closed)
      return j;
    let t;
    const { _reader: e } = this;
    for (; t = this._readNextMessageAndValidate(); )
      if (t.isSchema())
        this.reset(t.header());
      else if (t.isRecordBatch()) {
        this._recordBatchIndex++;
        const i = t.header(), s = e.readMessageBody(t.bodyLength);
        return { done: !1, value: this._loadRecordBatch(i, s) };
      } else if (t.isDictionaryBatch()) {
        this._dictionaryIndex++;
        const i = t.header(), s = e.readMessageBody(t.bodyLength), r = this._loadDictionaryBatch(i, s);
        this.dictionaries.set(i.id, r);
      }
    return this.schema && this._recordBatchIndex === 0 ? (this._recordBatchIndex++, { done: !1, value: new ua(this.schema) }) : this.return();
  }
  _readNextMessageAndValidate(t) {
    return this._reader.readMessage(t);
  }
}
class Wn extends ga {
  constructor(t, e) {
    super(e), this._reader = new au(this._handle = t);
  }
  isAsync() {
    return !0;
  }
  isStream() {
    return !0;
  }
  [Symbol.asyncIterator]() {
    return this;
  }
  cancel() {
    return O(this, void 0, void 0, function* () {
      !this.closed && (this.closed = !0) && (yield this.reset()._reader.return(), this._reader = null, this.dictionaries = null);
    });
  }
  open(t) {
    return O(this, void 0, void 0, function* () {
      return this.closed || (this.autoDestroy = ba(this, t), this.schema || (this.schema = yield this._reader.readSchema()) || (yield this.cancel())), this;
    });
  }
  throw(t) {
    return O(this, void 0, void 0, function* () {
      return !this.closed && this.autoDestroy && (this.closed = !0) ? yield this.reset()._reader.throw(t) : j;
    });
  }
  return(t) {
    return O(this, void 0, void 0, function* () {
      return !this.closed && this.autoDestroy && (this.closed = !0) ? yield this.reset()._reader.return(t) : j;
    });
  }
  next() {
    return O(this, void 0, void 0, function* () {
      if (this.closed)
        return j;
      let t;
      const { _reader: e } = this;
      for (; t = yield this._readNextMessageAndValidate(); )
        if (t.isSchema())
          yield this.reset(t.header());
        else if (t.isRecordBatch()) {
          this._recordBatchIndex++;
          const i = t.header(), s = yield e.readMessageBody(t.bodyLength);
          return { done: !1, value: this._loadRecordBatch(i, s) };
        } else if (t.isDictionaryBatch()) {
          this._dictionaryIndex++;
          const i = t.header(), s = yield e.readMessageBody(t.bodyLength), r = this._loadDictionaryBatch(i, s);
          this.dictionaries.set(i.id, r);
        }
      return this.schema && this._recordBatchIndex === 0 ? (this._recordBatchIndex++, { done: !1, value: new ua(this.schema) }) : yield this.return();
    });
  }
  _readNextMessageAndValidate(t) {
    return O(this, void 0, void 0, function* () {
      return yield this._reader.readMessage(t);
    });
  }
}
class ma extends Hn {
  get footer() {
    return this._footer;
  }
  get numDictionaries() {
    return this._footer ? this._footer.numDictionaries : 0;
  }
  get numRecordBatches() {
    return this._footer ? this._footer.numRecordBatches : 0;
  }
  constructor(t, e) {
    super(t instanceof Vs ? t : new Vs(t), e);
  }
  isSync() {
    return !0;
  }
  isFile() {
    return !0;
  }
  open(t) {
    if (!this.closed && !this._footer) {
      this.schema = (this._footer = this._readFooter()).schema;
      for (const e of this._footer.dictionaryBatches())
        e && this._readDictionaryBatch(this._dictionaryIndex++);
    }
    return super.open(t);
  }
  readRecordBatch(t) {
    var e;
    if (this.closed)
      return null;
    this._footer || this.open();
    const i = (e = this._footer) === null || e === void 0 ? void 0 : e.getRecordBatch(t);
    if (i && this._handle.seek(i.offset)) {
      const s = this._reader.readMessage(U.RecordBatch);
      if (s?.isRecordBatch()) {
        const r = s.header(), o = this._reader.readMessageBody(s.bodyLength);
        return this._loadRecordBatch(r, o);
      }
    }
    return null;
  }
  _readDictionaryBatch(t) {
    var e;
    const i = (e = this._footer) === null || e === void 0 ? void 0 : e.getDictionaryBatch(t);
    if (i && this._handle.seek(i.offset)) {
      const s = this._reader.readMessage(U.DictionaryBatch);
      if (s?.isDictionaryBatch()) {
        const r = s.header(), o = this._reader.readMessageBody(s.bodyLength), a = this._loadDictionaryBatch(r, o);
        this.dictionaries.set(r.id, a);
      }
    }
  }
  _readFooter() {
    const { _handle: t } = this, e = t.size - pa, i = t.readInt32(e), s = t.readAt(e - i, i);
    return Xi.decode(s);
  }
  _readNextMessageAndValidate(t) {
    var e;
    if (this._footer || this.open(), this._footer && this._recordBatchIndex < this.numRecordBatches) {
      const i = (e = this._footer) === null || e === void 0 ? void 0 : e.getRecordBatch(this._recordBatchIndex);
      if (i && this._handle.seek(i.offset))
        return this._reader.readMessage(t);
    }
    return null;
  }
}
class mu extends Wn {
  get footer() {
    return this._footer;
  }
  get numDictionaries() {
    return this._footer ? this._footer.numDictionaries : 0;
  }
  get numRecordBatches() {
    return this._footer ? this._footer.numRecordBatches : 0;
  }
  constructor(t, ...e) {
    const i = typeof e[0] != "number" ? e.shift() : void 0, s = e[0] instanceof Map ? e.shift() : void 0;
    super(t instanceof kn ? t : new kn(t, i), s);
  }
  isFile() {
    return !0;
  }
  isAsync() {
    return !0;
  }
  open(t) {
    const e = Object.create(null, {
      open: { get: () => super.open }
    });
    return O(this, void 0, void 0, function* () {
      if (!this.closed && !this._footer) {
        this.schema = (this._footer = yield this._readFooter()).schema;
        for (const i of this._footer.dictionaryBatches())
          i && (yield this._readDictionaryBatch(this._dictionaryIndex++));
      }
      return yield e.open.call(this, t);
    });
  }
  readRecordBatch(t) {
    return O(this, void 0, void 0, function* () {
      var e;
      if (this.closed)
        return null;
      this._footer || (yield this.open());
      const i = (e = this._footer) === null || e === void 0 ? void 0 : e.getRecordBatch(t);
      if (i && (yield this._handle.seek(i.offset))) {
        const s = yield this._reader.readMessage(U.RecordBatch);
        if (s?.isRecordBatch()) {
          const r = s.header(), o = yield this._reader.readMessageBody(s.bodyLength);
          return this._loadRecordBatch(r, o);
        }
      }
      return null;
    });
  }
  _readDictionaryBatch(t) {
    return O(this, void 0, void 0, function* () {
      var e;
      const i = (e = this._footer) === null || e === void 0 ? void 0 : e.getDictionaryBatch(t);
      if (i && (yield this._handle.seek(i.offset))) {
        const s = yield this._reader.readMessage(U.DictionaryBatch);
        if (s?.isDictionaryBatch()) {
          const r = s.header(), o = yield this._reader.readMessageBody(s.bodyLength), a = this._loadDictionaryBatch(r, o);
          this.dictionaries.set(r.id, a);
        }
      }
    });
  }
  _readFooter() {
    return O(this, void 0, void 0, function* () {
      const { _handle: t } = this;
      t._pending && (yield t._pending);
      const e = t.size - pa, i = yield t.readInt32(e), s = yield t.readAt(e - i, i);
      return Xi.decode(s);
    });
  }
  _readNextMessageAndValidate(t) {
    return O(this, void 0, void 0, function* () {
      if (this._footer || (yield this.open()), this._footer && this._recordBatchIndex < this.numRecordBatches) {
        const e = this._footer.getRecordBatch(this._recordBatchIndex);
        if (e && (yield this._handle.seek(e.offset)))
          return yield this._reader.readMessage(t);
      }
      return null;
    });
  }
}
class bu extends Hn {
  constructor(t, e) {
    super(t, e);
  }
  _loadVectors(t, e, i) {
    return new Bl(e, t.nodes, t.buffers, this.dictionaries, this.schema.metadataVersion).visitMany(i);
  }
}
function ba(n, t) {
  return t && typeof t.autoDestroy == "boolean" ? t.autoDestroy : n.autoDestroy;
}
function* ks(n) {
  const t = Vt.from(n);
  try {
    if (!t.open({ autoDestroy: !1 }).closed)
      do
        yield t;
      while (!t.reset().open().closed);
  } finally {
    t.cancel();
  }
}
function Ps(n) {
  return Nt(this, arguments, function* () {
    const e = yield F(Vt.from(n));
    try {
      if (!(yield F(e.open({ autoDestroy: !1 }))).closed)
        do
          yield yield F(e);
        while (!(yield F(e.reset().open())).closed);
    } finally {
      yield F(e.cancel());
    }
  });
}
function _u(n) {
  return new $n(new bu(n));
}
function vu(n) {
  const t = n.peek(dn + 7 & -8);
  return t && t.byteLength >= 4 ? cs(t) ? new ya(new ma(n.read())) : new $n(new Hn(n)) : new $n(new Hn((function* () {
  })()));
}
function wu(n) {
  return O(this, void 0, void 0, function* () {
    const t = yield n.peek(dn + 7 & -8);
    return t && t.byteLength >= 4 ? cs(t) ? new ya(new ma(yield n.read())) : new Yn(new Wn(n)) : new Yn(new Wn((function() {
      return Nt(this, arguments, function* () {
      });
    })()));
  });
}
function Iu(n) {
  return O(this, void 0, void 0, function* () {
    const { size: t } = yield n.stat(), e = new kn(n, t);
    return t >= lu && cs(yield e.readAt(0, dn + 7 & -8)) ? new gu(new mu(e)) : new Yn(new Wn(e));
  });
}
function Si(n) {
  const t = Vt.from(n);
  return qe(t) ? t.then((e) => Si(e)) : t.isAsync() ? t.readAll().then((e) => new ot(e)) : new ot(t.readAll());
}
const li = ou({
  id: ge([1, 2, 3, 4, 5], new Jt()),
  name: ge(["alpha", "beta", "gamma", "delta", "epsilon"]),
  value: ge([10.5, 22.3, 7.8, 99.1, 45], new Jn()),
  active: ge([!0, !1, !0, !0, !1]),
  category: ge(["A", "B", "A", "C", "B"])
});
function js(n, t = 100) {
  const i = n.schema.fields.map((c) => c.name), s = `<thead><tr>${i.map((c) => `<th>${be(String(c))}</th>`).join("")}</tr></thead>`, r = Math.min(n.numRows, t), o = [];
  for (let c = 0; c < r; c++) {
    const u = i.map((d) => {
      const h = n.getChild(d)?.get(c);
      return `<td>${be(Su(h))}</td>`;
    });
    o.push(`<tr>${u.join("")}</tr>`);
  }
  const a = `<tbody>${o.join("")}</tbody>`;
  return `<table>${s}${a}</table>`;
}
function Su(n) {
  return n == null ? "" : typeof n == "boolean" ? n ? "true" : "false" : typeof n == "bigint" ? n.toString() : n instanceof Date ? n.toISOString() : typeof n == "object" ? JSON.stringify(n) : String(n);
}
function be(n) {
  return n.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
const Bu = (
  /* css */
  `
  :host {
    display: block;
    box-sizing: border-box;
    font-family: system-ui, -apple-system, 'Segoe UI', sans-serif;
    font-size: 13px;
    color: var(--nubi-fg, #e2e8f0);
    background: var(--nubi-bg, #0f1117);
    border: 1px solid var(--nubi-border, #2d3748);
    border-radius: 8px;
    overflow: hidden;
  }

  .nubi-wrap {
    width: 100%;
    height: 100%;
    overflow: auto;
    position: relative;
  }

  .nubi-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px;
    background: var(--nubi-accent, #1e2433);
    border-bottom: 1px solid var(--nubi-border, #2d3748);
    font-size: 11px;
    color: var(--nubi-fg, #e2e8f0);
    opacity: 0.8;
    gap: 8px;
  }

  .nubi-toolbar .nubi-title {
    font-weight: 600;
    letter-spacing: 0.02em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    flex: 1;
  }

  .nubi-badge {
    font-size: 10px;
    padding: 2px 6px;
    border-radius: 4px;
    font-weight: 600;
    letter-spacing: 0.04em;
    white-space: nowrap;
    flex-shrink: 0;
  }

  .nubi-badge.hit  { background: #064e3b; color: #6ee7b7; }
  .nubi-badge.miss { background: #1e3a5f; color: #93c5fd; }
  .nubi-badge.sample {
    background: #422006;
    color: #fed7aa;
  }

  .nubi-sample-note {
    font-size: 11px;
    color: #f97316;
    padding: 4px 12px 6px;
    background: #1a1208;
    border-bottom: 1px solid #7c2d12;
    text-align: center;
  }

  .nubi-table-wrap {
    overflow: auto;
    max-height: calc(100% - 72px);
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    line-height: 1.4;
  }

  thead tr {
    background: var(--nubi-accent, #1e2433);
    position: sticky;
    top: 0;
    z-index: 1;
  }

  thead th {
    padding: 7px 10px;
    text-align: left;
    font-weight: 600;
    font-size: 11px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--nubi-fg, #e2e8f0);
    opacity: 0.7;
    border-bottom: 1px solid var(--nubi-border, #2d3748);
    white-space: nowrap;
  }

  tbody tr {
    border-bottom: 1px solid var(--nubi-border, #2d3748);
    transition: background 0.1s;
  }

  tbody tr:hover {
    background: rgba(255, 255, 255, 0.04);
  }

  tbody td {
    padding: 6px 10px;
    color: var(--nubi-fg, #e2e8f0);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
    max-width: 260px;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .nubi-widget-title {
    margin: 0;
    padding: 10px 12px 6px;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.03em;
    color: var(--nubi-fg, #e2e8f0);
    opacity: 0.85;
  }

  .nubi-widget-error {
    padding: 8px 12px;
    font-size: 11px;
    color: #f87171;
  }

  .nubi-empty {
    padding: 32px;
    text-align: center;
    color: var(--nubi-fg, #e2e8f0);
    opacity: 0.55;
    font-size: 12px;
  }

  .nubi-loading {
    padding: 32px;
    text-align: center;
    color: var(--nubi-fg, #e2e8f0);
    opacity: 0.5;
  }

  .nubi-loading::after {
    content: '';
    display: inline-block;
    width: 14px;
    height: 14px;
    border: 2px solid currentColor;
    border-top-color: transparent;
    border-radius: 50%;
    vertical-align: -3px;
    margin-left: 8px;
    animation: nubi-spin 0.8s linear infinite;
  }

  @keyframes nubi-spin {
    to { transform: rotate(360deg); }
  }

  .nubi-error-msg {
    padding: 16px;
    color: #f87171;
    font-size: 12px;
    background: #1c0a0a;
    border-radius: 4px;
    margin: 8px;
  }

  .nubi-footer {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    padding: 4px 10px;
    font-size: 10px;
    opacity: 0.45;
    border-top: 1px solid var(--nubi-border, #2d3748);
    gap: 8px;
  }
`
);
class Au extends HTMLElement {
  // ---- Custom element lifecycle ------------------------------------------
  static get observedAttributes() {
    return ["query", "dashboard-id", "token", "get-token", "backend", "theme"];
  }
  constructor() {
    super(), this._shadow = this.attachShadow({ mode: "open" }), this._abortController = null, this._rendering = !1;
  }
  connectedCallback() {
    this._render();
  }
  disconnectedCallback() {
    this._abort();
  }
  attributeChangedCallback(t, e, i) {
    e !== i && this.isConnected && this._render();
  }
  // ---- Internal helpers --------------------------------------------------
  _abort() {
    this._abortController && (this._abortController.abort(), this._abortController = null);
  }
  /**
   * Resolve a JWT token from:
   *  1. The `token` attribute (static string).
   *  2. The `get-token` attribute — a function name on `window`.
   *
   * @returns {Promise<string | null>}
   */
  async _resolveToken() {
    const t = this.getAttribute("token");
    if (t) return t;
    const e = this.getAttribute("get-token");
    if (!e) return null;
    const i = window[e];
    if (typeof i != "function")
      return console.warn(`[nubi-dashboard] window.${e} is not a function`), null;
    try {
      return await i() ?? null;
    } catch (s) {
      return console.warn("[nubi-dashboard] getToken() threw:", s.message), null;
    }
  }
  /** @returns {string} */
  _backendUrl() {
    return (this.getAttribute("backend") || "http://localhost:8000").replace(/\/+$/, "").replace(/\/api\/v1$/, "");
  }
  // ---- DOM helpers -------------------------------------------------------
  /** Show the loading spinner. */
  _showLoading() {
    const t = this._shadow.querySelector(".nubi-table-wrap");
    t && (t.innerHTML = '<div class="nubi-loading">Running query</div>');
    const e = this._shadow.querySelector(".nubi-sample-note");
    e && (e.style.display = "none");
  }
  /** Render table data into the shadow DOM. */
  _showTable(t, { cacheStatus: e = "MISS", elapsedMs: i = 0, isSample: s = !1 } = {}) {
    const r = this._shadow.querySelector(".nubi-badge");
    r && (s ? (r.textContent = "SAMPLE", r.className = "nubi-badge sample") : e === "HIT" ? (r.textContent = "CACHE HIT", r.className = "nubi-badge hit") : (r.textContent = "LIVE", r.className = "nubi-badge miss"));
    const o = this._shadow.querySelector(".nubi-sample-note");
    o && (o.style.display = s ? "block" : "none");
    const a = this._shadow.querySelector(".nubi-table-wrap");
    a && (a.innerHTML = js(t, 100));
    const c = this._shadow.querySelector(".nubi-footer");
    c && (c.textContent = `${t.numRows.toLocaleString()} row${t.numRows !== 1 ? "s" : ""} · ${i}ms`);
  }
  /** Set the toolbar title text (truncated like the scaffold default). */
  _setTitle(t) {
    const e = this._shadow.querySelector(".nubi-title");
    if (!e) return;
    const i = String(t ?? "");
    e.textContent = i.length > 60 ? i.slice(0, 57) + "…" : i;
  }
  /** Show an error message (only used as last resort; usually we fall to sample). */
  _showError(t) {
    const e = this._shadow.querySelector(".nubi-table-wrap");
    e && (e.innerHTML = `<div class="nubi-error-msg">Error: ${be(t)}</div>`);
  }
  // ---- Shadow DOM scaffold -----------------------------------------------
  _ensureScaffold() {
    if (this._shadow.querySelector(".nubi-wrap")) return;
    const t = document.createElement("style");
    t.textContent = Bu;
    const e = this.getAttribute("query") || this.getAttribute("dashboard-id") || "Query", i = e.length > 60 ? e.slice(0, 57) + "…" : e;
    this._shadow.innerHTML = "", this._shadow.appendChild(t), this._shadow.innerHTML += /* html */
    `
      <div class="nubi-wrap">
        <div class="nubi-toolbar">
          <span class="nubi-title">${be(i)}</span>
          <span class="nubi-badge miss">…</span>
        </div>
        <div class="nubi-sample-note" style="display:none">
          preview (sample data) — connect a backend to load real results
        </div>
        <div class="nubi-table-wrap">
          <div class="nubi-loading">Running query</div>
        </div>
        <div class="nubi-footer"></div>
      </div>
    `, this._shadow.insertBefore(t, this._shadow.firstChild);
  }
  // ---- Dashboard descriptor render -----------------------------------------
  /**
   * Fetch the embed descriptor for `dashboard-id` and render its widgets.
   *
   * Each widget that references a registered query id is executed via
   * POST /api/v1/query ({ query_id }) and rendered as a stacked section with
   * the widget's title as a heading. Returns `true` when the descriptor was
   * rendered (including the empty state); `false` on a config fetch/auth
   * failure so the caller can fall back to the sample table.
   *
   * @param {{ ac: AbortController, t0: number, backend: string,
   *           token: string | null, dashboardId: string }} opts
   * @returns {Promise<boolean>}
   */
  async _renderDashboard({ ac: t, t0: e, backend: i, token: s, dashboardId: r }) {
    const o = { Accept: "application/json" }, a = {
      "Content-Type": "application/json",
      Accept: "application/vnd.apache.arrow.stream"
    };
    s && (o.Authorization = `Bearer ${s}`, a.Authorization = `Bearer ${s}`);
    let c;
    try {
      const Y = await fetch(`${i}/api/v1/embed/config/${encodeURIComponent(r)}`, {
        method: "GET",
        headers: o,
        credentials: "omit",
        signal: t.signal
      });
      if (t.signal.aborted) return !0;
      if (!Y.ok) {
        const yt = `Embed config API returned HTTP ${Y.status}`;
        return console.warn(`[nubi-dashboard] ${yt} — showing sample`), this.dispatchEvent(new CustomEvent("nubi:error", {
          bubbles: !0,
          composed: !0,
          detail: { message: yt }
        })), !1;
      }
      c = await Y.json();
    } catch (Y) {
      return Y.name === "AbortError" ? !0 : (console.warn("[nubi-dashboard] Embed config fetch error — showing sample:", Y.message), this.dispatchEvent(new CustomEvent("nubi:error", {
        bubbles: !0,
        composed: !0,
        detail: { message: Y.message }
      })), !1);
    }
    if (t.signal.aborted) return !0;
    this._setTitle(c.title || r);
    const d = (Array.isArray(c.widgets) ? c.widgets : []).filter((Y) => Y && (Y.query_id || Y.props?.query_id));
    if (d.length === 0) {
      const Y = this._shadow.querySelector(".nubi-table-wrap");
      Y && (Y.innerHTML = '<div class="nubi-empty">This dashboard has no embeddable widgets yet — add a widget backed by a registered query to see data here.</div>');
      const yt = this._shadow.querySelector(".nubi-badge");
      yt && (yt.textContent = "EMPTY", yt.className = "nubi-badge miss");
      const Ee = this._shadow.querySelector(".nubi-footer");
      return Ee && (Ee.textContent = "0 widgets"), this.dispatchEvent(new CustomEvent("nubi:ready", {
        bubbles: !0,
        composed: !0,
        detail: { rowCount: 0 }
      })), !0;
    }
    const h = [];
    let N = 0, B = !1;
    for (let Y = 0; Y < d.length; Y++) {
      const yt = d[Y], Ee = yt.query_id || yt.props?.query_id, _a = yt.title || yt.props?.title || `Widget ${Y + 1}`, ls = `<h3 class="nubi-widget-title">${be(String(_a))}</h3>`;
      try {
        const Yt = await fetch(`${i}/api/v1/query`, {
          method: "POST",
          headers: a,
          body: JSON.stringify({ query_id: Ee }),
          credentials: "omit",
          signal: t.signal
        });
        if (t.signal.aborted) return !0;
        if (!Yt.ok)
          throw new Error(`Query API returned HTTP ${Yt.status}`);
        (Yt.headers.get("X-Nubi-Cache") ?? "MISS") !== "HIT" && (B = !0);
        const va = await Yt.arrayBuffer();
        if (t.signal.aborted) return !0;
        const us = Si(new Uint8Array(va));
        N += us.numRows, h.push(ls + js(us, 100));
      } catch (Yt) {
        if (Yt.name === "AbortError") return !0;
        console.warn(`[nubi-dashboard] Widget query ${Ee} failed:`, Yt.message), h.push(`${ls}<div class="nubi-widget-error">Failed to load: ${be(Yt.message)}</div>`), B = !0;
      }
    }
    const z = Math.round(performance.now() - e), wt = this._shadow.querySelector(".nubi-table-wrap");
    wt && (wt.innerHTML = h.join(""));
    const re = this._shadow.querySelector(".nubi-sample-note");
    re && (re.style.display = "none");
    const ct = this._shadow.querySelector(".nubi-badge");
    ct && (ct.textContent = B ? "LIVE" : "CACHE HIT", ct.className = B ? "nubi-badge miss" : "nubi-badge hit");
    const hn = this._shadow.querySelector(".nubi-footer");
    return hn && (hn.textContent = `${d.length} widget${d.length !== 1 ? "s" : ""} · ${N.toLocaleString()} row${N !== 1 ? "s" : ""} · ${z}ms`), this.dispatchEvent(new CustomEvent("nubi:query-run", {
      bubbles: !0,
      composed: !0,
      detail: { rowCount: N, cacheStatus: B ? "MISS" : "HIT", elapsedMs: z, sample: !1 }
    })), this.dispatchEvent(new CustomEvent("nubi:ready", {
      bubbles: !0,
      composed: !0,
      detail: { rowCount: N }
    })), !0;
  }
  // ---- Core render -------------------------------------------------------
  async _render() {
    this._abort();
    const t = new AbortController();
    this._abortController = t, this._rendering = !0, this._ensureScaffold(), this._showLoading();
    const e = performance.now(), i = this.getAttribute("query") || "", s = this.getAttribute("dashboard-id") || "", r = this._backendUrl();
    let o;
    try {
      o = await this._resolveToken();
    } catch {
      o = null;
    }
    if (t.signal.aborted) return;
    if (!i && s && r && (await this._renderDashboard({ ac: t, t0: e, backend: r, token: o, dashboardId: s }) || t.signal.aborted)) {
      this._rendering = !1;
      return;
    }
    if (i && r)
      try {
        const c = {
          "Content-Type": "application/json",
          Accept: "application/vnd.apache.arrow.stream"
        };
        o && (c.Authorization = `Bearer ${o}`);
        const u = await fetch(`${r}/api/v1/query`, {
          method: "POST",
          headers: c,
          body: JSON.stringify({ sql: i }),
          // credentials: 'omit' — cross-origin embed; no cookies sent
          credentials: "omit",
          signal: t.signal
        });
        if (t.signal.aborted) return;
        if (u.ok) {
          const h = u.headers.get("X-Nubi-Cache") ?? "MISS", N = await u.arrayBuffer();
          if (t.signal.aborted) return;
          const B = Si(new Uint8Array(N)), z = Math.round(performance.now() - e);
          this._showTable(B, { cacheStatus: h, elapsedMs: z, isSample: !1 }), this.dispatchEvent(new CustomEvent("nubi:query-run", {
            bubbles: !0,
            composed: !0,
            detail: { rowCount: B.numRows, cacheStatus: h, elapsedMs: z, sample: !1 }
          })), this.dispatchEvent(new CustomEvent("nubi:ready", {
            bubbles: !0,
            composed: !0,
            detail: { rowCount: B.numRows }
          })), this._rendering = !1;
          return;
        }
        const d = `Query API returned HTTP ${u.status}`;
        console.warn(`[nubi-dashboard] ${d} — showing sample`), this.dispatchEvent(new CustomEvent("nubi:error", {
          bubbles: !0,
          composed: !0,
          detail: { message: d }
        }));
      } catch (c) {
        if (c.name === "AbortError") return;
        console.warn("[nubi-dashboard] Fetch/parse error — showing sample:", c.message), this.dispatchEvent(new CustomEvent("nubi:error", {
          bubbles: !0,
          composed: !0,
          detail: { message: c.message }
        }));
      }
    if (t.signal.aborted) return;
    const a = Math.round(performance.now() - e);
    this._showTable(li, { cacheStatus: "SAMPLE", elapsedMs: a, isSample: !0 }), this.dispatchEvent(new CustomEvent("nubi:query-run", {
      bubbles: !0,
      composed: !0,
      detail: { rowCount: li.numRows, cacheStatus: "SAMPLE", elapsedMs: a, sample: !0 }
    })), this.dispatchEvent(new CustomEvent("nubi:ready", {
      bubbles: !0,
      composed: !0,
      detail: { rowCount: li.numRows }
    })), this._rendering = !1;
  }
}
customElements.define("nubi-dashboard", Au);
export {
  Au as NubiDashboard
};
//# sourceMappingURL=nubi-dashboard.es.js.map
